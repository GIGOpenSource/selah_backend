#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""VIP / IAP 验单。

业务要点：
- activate_vip：未过期则在现有 vip_expire_at 上叠加天数；重置当日 VIP 额度。
- verify：商店验签；同 transaction_id 幂等；他人收据 409。
- restore：按 transaction / original_transaction 找回未过期收据并挂到当前 user。
"""
import hashlib
import logging
import os
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone as dj_tz
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import IapReceipt, User
from Apps.views.membership.serializers import IapVerifySerializer, IapRestoreSerializer
from tools.permissions import IsTokenValid
from tools.selah_quota import (
    get_config, user_public_dict, refresh_vip_daily_if_needed,
    normalize_app_key, get_config_context, config_as_bool,
)
from tools.membership_products import list_enabled_products_for_app, resolve_product
from tools.iap_verify import (
    verify_store_receipt, sandbox_allowed, google_play_package,
    parse_google_rtdn_push, fetch_google_subscription_v2, acknowledge_google_subscription,
    parse_apple_server_notification, user_id_from_apple_account_token,
)
from tools.utils import ApiResponse

logger = logging.getLogger(__name__)


def activate_vip(user: User, days: int, is_trial: bool, product_id: str):
    """开通/续费 VIP：叠加到期日并重置当日额度。

    Args:
        days: 本次增加的有效天数（试用或套餐时长）。
        is_trial: 是否标记试用态（影响运营展示，不单独改额度账本类型）。
        product_id: 商店商品 ID，仅日志。

    业务说明:
        - 若当前仍在有效期内，base=现有 vip_expire_at，否则从 now 起算（避免过期后仍从旧到期日续）。
        - 开通后立即把 vip_daily_remaining 拉满到 vip_daily_quota，并记重置日。
    """
    now = dj_tz.now()
    # 未过期续费：在旧到期日上叠加；已过期/无到期：从现在起算
    base = user.vip_expire_at if user.vip_expire_at and user.vip_expire_at > now else now
    user.vip_level = User.VIP_VIP
    user.is_trial = is_trial
    user.vip_expire_at = base + timedelta(days=days)
    user.vip_daily_remaining = int(get_config('vip_daily_quota', 100))
    from tools.selah_quota import user_local_today
    user.vip_daily_reset_date = user_local_today(user)
    user.save()
    refresh_vip_daily_if_needed(user)
    logger.info('activate_vip user=%s trial=%s until=%s product=%s', user.id, is_trial, user.vip_expire_at, product_id)
    return user


@extend_schema(tags=[_('Selah会员')])
class MembershipViewSet(ViewSet):
    """会员：套餐页、IAP 验单开通、恢复购买。"""

    permission_classes = [IsTokenValid]

    @extend_schema(summary=_('会员页信息'))
    @action(detail=False, methods=['get'], url_path='plans')
    def plans(self, request):
        """会员页：当前等级、试用天数与启用商品。

        业务说明:
            products 来自 MembershipProduct 启用列表；benefits 文案含动态日额度。
            price_display 按用户/请求语言从 price_display_i18n 解析。
        """
        from tools.selah_quota import set_config_context_from_request, expire_vip_if_needed
        set_config_context_from_request(request, prefer_user=True)
        used_trial = _user_has_used_trial(request.user)
        _remember_trial(request.user)
        user = expire_vip_if_needed(request.user)
        products = list_enabled_products_for_app()
        trial_days = int(get_config('trial_days', 3))
        benefits = get_config('vip_benefits')
        if not isinstance(benefits, list) or not benefits:
            benefits = [
                'Unlock VIP topics & wallpapers',
                'Hide watermark on share cards',
                '{vip_daily_quota} AI messages / day',
            ]
        # 动态替换额度占位
        benefits = [
            str(b).replace('{vip_daily_quota}', str(get_config('vip_daily_quota', 100)))
            for b in benefits
        ]
        cta = (get_config('vip_cta_text') or '').strip() or 'Try for $0.00'
        return ApiResponse(data={
            'vip_level': 'vip' if user.is_vip_active else 'free',
            'is_trial': bool(user.is_trial and user.is_vip_active),
            'trial_available': (not used_trial) and (not user.is_vip_active),
            'vip_expire_at': user.vip_expire_at.isoformat() if user.vip_expire_at else None,
            'trial_days': trial_days,
            'products': products,
            'benefits': benefits,
            'cta_text': cta,
            'vip_daily_quota': int(get_config('vip_daily_quota', 100)),
        })

    @extend_schema(summary=_('开通试用'))
    @action(detail=False, methods=['post'], url_path='trial')
    def trial(self, request):
        """没试用过、也不是会员时，按 trial_days 开通一次。不走商店。"""
        from tools.selah_quota import set_config_context_from_request, expire_vip_if_needed
        set_config_context_from_request(request, prefer_user=True)
        _remember_trial(request.user)
        user = expire_vip_if_needed(request.user)
        if user.is_vip_active or _user_has_used_trial(user):
            return ApiResponse(message=_('试用不可用'), code=400)
        days = int(get_config('trial_days', 3))
        try:
            with transaction.atomic():
                locked = User.objects.select_for_update().get(pk=user.pk)
                if locked.is_vip_active or _user_has_used_trial(locked):
                    return ApiResponse(message=_('试用不可用'), code=400)
                locked = activate_vip(locked, days=days, is_trial=True, product_id='trial')
                ctx_app, _lang, _plat = get_config_context()
                IapReceipt.objects.create(
                    transaction_id='trial-%s' % locked.id,
                    user_id=locked.id,
                    app_key=normalize_app_key(ctx_app) or normalize_app_key(locked.app_key or '') or 'selah',
                    platform='trial',
                    product_id='trial',
                    original_transaction_id='',
                    receipt_data='',
                    is_trial=True,
                    amount_cents=0,
                    currency='USD',
                    expire_at=locked.vip_expire_at,
                    status='active',
                )
        except IntegrityError:
            return ApiResponse(message=_('试用不可用'), code=400)
        return ApiResponse(data={
            'trial_available': False,
            'user': user_public_dict(locked),
        }, message=_('试用已开通'))

    @extend_schema(summary=_('验单开通/续费'))
    @action(detail=False, methods=['post'], url_path='verify')
    def verify(self, request):
        """
        IAP 验单开通/续费。

        业务说明:
            - product 必须能 resolve，否则 400。
            - sandbox 受 DEBUG + iap_sandbox_allowed（+ 生产 ALLOW_IAP_SANDBOX）门禁。
            - 非 sandbox 必须带 receipt_data；商店正式 API 见 tools.iap_verify。
            - 同用户同 transaction 且 active → 幂等 already，不再叠加天数。
            - is_trial 用商品 trial_days 或全局 trial_days；正式购买用 duration_days。
        """
        ser = IapVerifySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        platform = data['platform']
        product_id = data['product_id']
        transaction_id = data['transaction_id']
        receipt_data = data.get('receipt_data') or ''
        is_trial = bool(data.get('is_trial'))
        sandbox = bool(data.get('sandbox'))
        pay_trigger = data.get('pay_trigger_source') or 'other'
        original_tx = data.get('original_transaction_id') or ''

        from tools.selah_quota import set_config_context_from_request, normalize_app_key
        set_config_context_from_request(request, prefer_user=True)
        logger.info(
            'iap verify start user=%s app_key=%s platform=%s product=%s tx=%s original_tx=%s '
            'sandbox=%s is_trial=%s pay_trigger=%s receipt_len=%s receipt_head=%s',
            getattr(request.user, 'id', None),
            getattr(request.user, 'app_key', '') or '',
            platform, product_id, transaction_id, original_tx or '-',
            sandbox, is_trial, pay_trigger, len(receipt_data),
            (receipt_data[:24] + '...') if len(receipt_data) > 24 else receipt_data,
        )
        if not config_as_bool('iap_verify_enabled', True):
            logger.warning('iap verify disabled user=%s', getattr(request.user, 'id', None))
            return ApiResponse(message=_('IAP verify disabled'), code=403)
        app = normalize_app_key(getattr(request.user, 'app_key', '') or '')
        product = resolve_product(platform, product_id, app_key=app)
        if not product:
            logger.warning(
                'iap verify unknown product user=%s app=%s platform=%s product=%s',
                getattr(request.user, 'id', None), app, platform, product_id,
            )
            return ApiResponse(message='unknown product_id', code=400)
        plan_days = int(getattr(product, 'duration_days', None) or 30)

        ok, msg, meta = verify_store_receipt(
            platform=platform,
            product_id=product_id,
            transaction_id=transaction_id,
            receipt_data=receipt_data,
            sandbox=sandbox,
            app_key=app,
        )
        logger.info(
            'iap verify store result user=%s ok=%s msg=%s mode=%s meta=%s',
            getattr(request.user, 'id', None), ok, msg,
            (meta or {}).get('mode') if isinstance(meta, dict) else None, meta,
        )
        if not ok:
            return ApiResponse(message=msg, code=400)

        # 沙盒无真收据：生成可追溯伪收据，满足非空约束
        if sandbox and not receipt_data:
            if not sandbox_allowed():
                logger.warning('iap verify sandbox blocked user=%s', getattr(request.user, 'id', None))
                return ApiResponse(message='sandbox IAP disabled', code=403)
            receipt_data = hashlib.sha256(f'{transaction_id}:{request.user.id}'.encode()).hexdigest()
            logger.info('iap verify sandbox fake receipt user=%s tx=%s', request.user.id, transaction_id)

        existing = IapReceipt.objects.filter(transaction_id=transaction_id).first()
        # 幂等：同一交易已挂本用户且 active，不重复叠加
        if existing and existing.user_id == request.user.id and existing.status == 'active':
            logger.info(
                'iap verify already user=%s tx=%s expire_at=%s',
                request.user.id, transaction_id, existing.expire_at,
            )
            return ApiResponse(data={
                'user': user_public_dict(request.user),
                'pay_trigger_source': pay_trigger,
                'already': True,
            }, message=_('已开通'))
        if existing and existing.user_id != request.user.id:
            logger.warning(
                'iap verify conflict tx=%s owner=%s requester=%s',
                transaction_id, existing.user_id, request.user.id,
            )
            return ApiResponse(message=_('该交易已绑定其他账号'), code=409)

        trial_override = getattr(product, 'trial_days', None)
        trial_days = int(trial_override if trial_override is not None else get_config('trial_days', 3))
        days = trial_days if is_trial else plan_days
        try:
            with transaction.atomic():
                locked = (
                    IapReceipt.objects.select_for_update()
                    .filter(transaction_id=transaction_id)
                    .first()
                )
                if locked and locked.user_id == request.user.id and locked.status == 'active':
                    logger.info('iap verify already(locked) user=%s tx=%s', request.user.id, transaction_id)
                    return ApiResponse(data={
                        'user': user_public_dict(request.user),
                        'pay_trigger_source': pay_trigger,
                        'already': True,
                    }, message=_('已开通'))
                if locked and locked.user_id != request.user.id:
                    logger.warning(
                        'iap verify conflict(locked) tx=%s owner=%s requester=%s',
                        transaction_id, locked.user_id, request.user.id,
                    )
                    return ApiResponse(message=_('该交易已绑定其他账号'), code=409)
                user = activate_vip(request.user, days=days, is_trial=is_trial, product_id=product_id)
                amount_cents = 0 if is_trial else int(getattr(product, 'price_cents', 0) or 0)
                currency = (getattr(product, 'currency', None) or 'USD')[:8]
                ctx_app, _lang, _plat = get_config_context()
                receipt_app = normalize_app_key(ctx_app) or normalize_app_key(getattr(user, 'app_key', '') or '')
                IapReceipt.objects.update_or_create(
                    transaction_id=transaction_id,
                    defaults={
                        'user_id': user.id,
                        'app_key': receipt_app,
                        'platform': platform,
                        'product_id': product_id,
                        'original_transaction_id': original_tx or transaction_id,
                        'receipt_data': receipt_data[:4000],
                        'is_trial': is_trial,
                        'amount_cents': amount_cents,
                        'currency': currency,
                        'expire_at': user.vip_expire_at,
                        'status': 'active',
                    },
                )
        except IntegrityError:
            logger.warning('iap verify integrity conflict tx=%s user=%s', transaction_id, request.user.id)
            return ApiResponse(message=_('该交易已绑定其他账号'), code=409)
        logger.info(
            'iap verify granted user=%s platform=%s product=%s tx=%s days=%s amount_cents=%s '
            'expire_at=%s vip_level=%s is_trial=%s',
            user.id, platform, product_id, transaction_id, days, amount_cents,
            user.vip_expire_at, user.vip_level, user.is_trial,
        )
        return ApiResponse(data={
            'user': user_public_dict(user),
            'pay_trigger_source': pay_trigger,
        }, message=_('开通成功'))

    @extend_schema(summary=_('恢复购买'))
    @action(detail=False, methods=['post'], url_path='restore')
    def restore(self, request):
        """按 original_transaction_id / transaction_id 找回订阅并挂到当前 user。

        业务说明:
            先精确 transaction_id，再按 original_transaction_id 取最新 active。
            已过期返回 400；成功则按剩余天数 activate_vip，并把收据 user_id 改挂当前用户
            （换机登录场景：订阅跟商店账号走，挂到新设备用户）。
        """
        ser = IapRestoreSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        from tools.selah_quota import set_config_context_from_request
        set_config_context_from_request(request, prefer_user=True)
        if not config_as_bool('iap_restore_enabled', True):
            return ApiResponse(message=_('IAP restore disabled'), code=403)
        tx = ser.validated_data.get('transaction_id') or ser.validated_data.get('original_transaction_id')
        row = IapReceipt.objects.filter(transaction_id=tx).first()
        if not row:
            row = IapReceipt.objects.filter(original_transaction_id=tx, status='active').order_by('-id').first()
        if not row:
            return ApiResponse(code=404, message=_('未找到可恢复订阅'), data={'restored': False})

        now = dj_tz.now()
        if row.expire_at and row.expire_at < now:
            return ApiResponse(code=400, message=_('订阅已过期'), data={'restored': False})

        user = request.user
        # 至少给 1 天，避免 expire_at 与 now 同日算成 0
        days_left = max(1, (row.expire_at - now).days) if row.expire_at else 30
        user = activate_vip(user, days=days_left, is_trial=row.is_trial, product_id=row.product_id)
        row.user_id = user.id
        row.save(update_fields=['user_id', 'updated_at'])
        return ApiResponse(data={'restored': True, 'user': user_public_dict(user)}, message=_('恢复成功'))

    @extend_schema(summary=_('Google 订阅通知'))
    @action(
        detail=False, methods=['post'], url_path='google-rtdn',
        permission_classes=[], authentication_classes=[],
    )
    def google_rtdn(self, request):
        """Play 实时通知。不登录。用 purchaseToken 向 Google 核对后再开通。"""
        from django.db.models import Q
        from rest_framework.response import Response

        note = parse_google_rtdn_push(request.data if isinstance(request.data, dict) else {})
        if not note:
            return ApiResponse(message='ignored')
        if note.get('testNotification'):
            logger.info('google rtdn test notification')
            return ApiResponse(message='ok')
        sub = note.get('subscriptionNotification') or {}
        if not isinstance(sub, dict) or not sub.get('purchaseToken'):
            return ApiResponse(message='ignored')

        package = (note.get('packageName') or '').strip()
        expected = google_play_package('selah')
        if expected and package and package != expected:
            logger.warning('google rtdn package mismatch got=%s expected=%s', package, expected)
            return ApiResponse(message='package ignored')
        token = str(sub.get('purchaseToken') or '')
        try:
            purchase = fetch_google_subscription_v2(package or expected, token)
        except Exception as e:
            logger.exception('google rtdn fetch failed: %s', e)
            return Response({'code': 503, 'message': 'google lookup failed'}, status=503)

        ext = purchase.get('externalAccountIdentifiers') or {}
        account = str(ext.get('obfuscatedExternalAccountId') or '').strip()
        user = User.objects.filter(id=int(account)).first() if account.isdigit() else None
        if not user:
            logger.error('google rtdn no user for account=%s', account)
            return ApiResponse(message='user not found')

        line = (purchase.get('lineItems') or [{}])[0] or {}
        product_id = str(line.get('productId') or sub.get('subscriptionId') or '')
        product = resolve_product('android', product_id, app_key=normalize_app_key(user.app_key or ''))
        if not product:
            logger.error('google rtdn unknown product=%s', product_id)
            return ApiResponse(message='unknown product')

        expire_at = _parse_google_time(line.get('expiryTime'))
        order_id = str(purchase.get('latestOrderId') or token[:120])
        state = str(purchase.get('subscriptionState') or '')
        entitled = state in (
            'SUBSCRIPTION_STATE_ACTIVE',
            'SUBSCRIPTION_STATE_IN_GRACE_PERIOD',
            'SUBSCRIPTION_STATE_CANCELED',
        )
        now = dj_tz.now()
        if entitled and expire_at and expire_at > now:
            _grant_vip_until(user, expire_at)
            amount = int(getattr(product, 'price_cents', 0) or 0)
            IapReceipt.objects.update_or_create(
                transaction_id=order_id[:128],
                defaults={
                    'user_id': user.id,
                    'app_key': normalize_app_key(user.app_key or '') or 'selah',
                    'platform': 'android',
                    'product_id': product_id,
                    'original_transaction_id': order_id.split('..')[0][:128],
                    'receipt_data': token[:4000],
                    'is_trial': False,
                    'amount_cents': amount,
                    'currency': (getattr(product, 'currency', None) or 'USD')[:8],
                    'expire_at': expire_at,
                    'status': 'active',
                },
            )
            ack = str(purchase.get('acknowledgementState') or '')
            if ack != 'ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED':
                acknowledge_google_subscription(package or expected, product_id, token)
            logger.info('google rtdn granted user=%s product=%s until=%s', user.id, product_id, expire_at)
            return ApiResponse(message='granted')

        IapReceipt.objects.filter(transaction_id=order_id[:128]).update(status='expired', updated_at=now)
        still = IapReceipt.objects.filter(user_id=user.id, status='active').filter(
            Q(expire_at__isnull=True) | Q(expire_at__gt=now)
        ).exists()
        if not still and user.is_vip_active:
            user.vip_level = User.VIP_FREE
            user.is_trial = False
            user.save(update_fields=['vip_level', 'is_trial', 'updated_at'])
        logger.info('google rtdn not entitled user=%s state=%s', user.id, state)
        return ApiResponse(message='updated')

    @extend_schema(summary=_('Apple 订阅通知'))
    @action(
        detail=False, methods=['post'], url_path='apple-notification',
        permission_classes=[], authentication_classes=[],
    )
    def apple_notification(self, request):
        """App Store 服务器通知。不登录。用签名里的用户标记开通。"""
        from django.db.models import Q

        body = request.data if isinstance(request.data, dict) else {}
        logger.info(
            'apple notification hit keys=%s signed_payload_len=%s',
            sorted(body.keys()) if isinstance(body, dict) else type(body).__name__,
            len(str(body.get('signedPayload') or '')) if isinstance(body, dict) else 0,
        )
        try:
            note = parse_apple_server_notification(body)
        except Exception as exc:
            logger.warning('apple notification rejected: %s', exc)
            return ApiResponse(message='invalid signature', code=400)
        if not note:
            logger.info('apple notification empty note')
            return ApiResponse(message='ignored')
        logger.info(
            'apple notification parsed type=%s subtype=%s uuid=%s env=%s',
            note.get('notificationType'), note.get('subtype'),
            note.get('notificationUUID'), note.get('data', {}).get('environment')
            if isinstance(note.get('data'), dict) else None,
        )
        if note.get('notificationType') == 'TEST':
            logger.info('apple notification test ok')
            return ApiResponse(message='ok')

        data = note.get('data') if isinstance(note.get('data'), dict) else {}
        bundle = str(data.get('bundleId') or '')
        if bundle and bundle != (os.getenv('GOOGLE_PLAY_PACKAGE') or 'com.example.app').strip():
            logger.warning('apple notification bundle ignored %s', bundle)
            return ApiResponse(message='package ignored')

        tx = note.get('_transaction') or {}
        logger.info(
            'apple notification tx id=%s original=%s product=%s account=%s '
            'expires=%s revoke=%s offer=%s price=%s currency=%s env=%s',
            tx.get('transactionId'), tx.get('originalTransactionId'), tx.get('productId'),
            tx.get('appAccountToken'), tx.get('expiresDate'), tx.get('revocationDate'),
            tx.get('offerDiscountType'), tx.get('price'), tx.get('currency'),
            tx.get('environment') or data.get('environment'),
        )
        if not tx.get('transactionId'):
            logger.info('apple notification ignored: no transactionId type=%s', note.get('notificationType'))
            return ApiResponse(message='ignored')
        user_id = user_id_from_apple_account_token(tx.get('appAccountToken'))
        user = User.objects.filter(id=user_id).first() if user_id else None
        if not user:
            logger.error(
                'apple notification no user token=%s decoded_user_id=%s tx=%s product=%s',
                tx.get('appAccountToken'), user_id, tx.get('transactionId'), tx.get('productId'),
            )
            return ApiResponse(message='user not found')

        product_id = str(tx.get('productId') or '')
        product = resolve_product('ios', product_id, app_key=normalize_app_key(user.app_key or ''))
        if not product:
            logger.error(
                'apple notification unknown product=%s user=%s app_key=%s',
                product_id, user.id, user.app_key,
            )
            return ApiResponse(message='unknown product')

        renewal = note.get('_renewal') or {}
        expire_at = _apple_millis(tx.get('expiresDate'))
        grace_at = _apple_millis(renewal.get('gracePeriodExpiresDate'))
        if grace_at and (not expire_at or grace_at > expire_at):
            expire_at = grace_at
        revoked = bool(tx.get('revocationDate'))
        now = dj_tz.now()
        order_id = str(tx.get('transactionId') or '')[:128]
        logger.info(
            'apple notification decide user=%s product=%s order=%s revoked=%s '
            'expire_at=%s grace_at=%s auto_renew=%s',
            user.id, product_id, order_id, revoked, expire_at, grace_at,
            renewal.get('autoRenewStatus'),
        )
        if not revoked and expire_at and expire_at > now:
            _grant_vip_until(user, expire_at)
            price = tx.get('price')
            if price is None:
                amount = int(getattr(product, 'price_cents', 0) or 0)
            else:
                amount = int(int(price) / 10)
            is_trial = str(tx.get('offerDiscountType') or '') == 'FREE_TRIAL'
            IapReceipt.objects.update_or_create(
                transaction_id=order_id,
                defaults={
                    'user_id': user.id,
                    'app_key': normalize_app_key(user.app_key or '') or 'selah',
                    'platform': 'ios',
                    'product_id': product_id,
                    'original_transaction_id': str(tx.get('originalTransactionId') or '')[:128],
                    'receipt_data': str(note.get('notificationUUID') or '')[:4000],
                    'is_trial': is_trial,
                    'amount_cents': 0 if is_trial else amount,
                    'currency': str(tx.get('currency') or getattr(product, 'currency', None) or 'USD')[:8],
                    'expire_at': expire_at,
                    'status': 'active',
                },
            )
            logger.info(
                'apple notification granted user=%s product=%s order=%s amount_cents=%s '
                'is_trial=%s until=%s vip_level=%s',
                user.id, product_id, order_id, 0 if is_trial else amount, is_trial,
                expire_at, user.vip_level,
            )
            return ApiResponse(message='granted')

        IapReceipt.objects.filter(transaction_id=order_id).update(
            status='revoked' if revoked else 'expired', updated_at=now,
        )
        still = IapReceipt.objects.filter(user_id=user.id, status='active').filter(
            Q(expire_at__isnull=True) | Q(expire_at__gt=now)
        ).exists()
        if not still and user.is_vip_active:
            user.vip_level = User.VIP_FREE
            user.is_trial = False
            user.save(update_fields=['vip_level', 'is_trial', 'updated_at'])
        logger.info(
            'apple notification not entitled user=%s type=%s subtype=%s order=%s '
            'revoked=%s expire_at=%s still_active_receipt=%s vip_level=%s',
            user.id, note.get('notificationType'), note.get('subtype'), order_id,
            revoked, expire_at, still, user.vip_level,
        )
        return ApiResponse(message='updated')


def _user_has_used_trial(user):
    """当前正在试用，或以前有过试用收据。过期后 is_trial 会被清掉，不能只看它。"""
    if user.is_trial:
        return True
    return IapReceipt.objects.filter(user_id=user.id, is_trial=True).exists()


def _remember_trial(user):
    """试用标记过期后会被清掉。先补一条试用收据，避免下次被当成没试用过。"""
    if not user.is_trial:
        return
    if IapReceipt.objects.filter(user_id=user.id, is_trial=True).exists():
        return
    IapReceipt.objects.get_or_create(
        transaction_id='trial-%s' % user.id,
        defaults={
            'user_id': user.id,
            'app_key': normalize_app_key(user.app_key or '') or 'selah',
            'platform': 'trial',
            'product_id': 'trial',
            'is_trial': True,
            'amount_cents': 0,
            'currency': 'USD',
            'expire_at': user.vip_expire_at,
            'status': 'active' if user.is_vip_active else 'expired',
        },
    )


def _apple_millis(value):
    from datetime import datetime, timezone as dt_tz
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=dt_tz.utc)
    except (TypeError, ValueError, OSError):
        return None


def _parse_google_time(value):
    from datetime import datetime, timezone as dt_tz
    if not value:
        return None
    text = str(value).replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_tz.utc)
    return parsed


def _grant_vip_until(user, expire_at):
    """把会员到期设成商店给的时间，不在原到期日上再叠天数。"""
    from tools.selah_quota import user_local_today
    now = dj_tz.now()
    current = user.vip_expire_at
    if current and current > expire_at and user.is_vip_active:
        expire_at = current
    user.vip_level = User.VIP_VIP
    user.is_trial = False
    user.vip_expire_at = expire_at
    user.vip_daily_remaining = int(get_config('vip_daily_quota', 100))
    user.vip_daily_reset_date = user_local_today(user)
    user.save()
    refresh_vip_daily_if_needed(user)
