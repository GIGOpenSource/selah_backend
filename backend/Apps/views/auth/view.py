#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah 认证 API：设备登录、OAuth 登录、资料与额度、登出、注销。

业务要点：
- device_login：按 (device_id, app_key) 找或建设备账号；与三方账号互不合并。
- oauth_login：按 (google_id|apple_id, app_key) 找或建三方账号；与设备账号互不合并、不抢 device_id。
- bind_oauth：已废弃（设备与三方只切换会话，不绑定并号）。
- delete_account / cancel_delete：用户自助注销（7 天冷静期 → 到期软删）。
- 配置上下文随用户 app_key/language/platform 设置，影响 get_config 回退链。
"""
import logging
import uuid

from django.db import transaction
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import User
from Apps.views.auth.serializers import (
    DeviceLoginSerializer,
    OAuthBindSerializer,
    OAuthLoginSerializer,
    UpdateProfileSerializer,
)
from tools.auth_helpers import persist_auth, clear_auth, get_token_from_request
from tools.permissions import IsTokenValid
from tools.account_deletion import (
    request_account_deletion,
    cancel_account_deletion,
)
from tools.selah_quota import (
    grant_newcomer_bonus, user_public_dict, ensure_default_configs, get_config,
    set_config_context_from_user, config_as_bool, set_config_context,
)
from tools.selah_enums import (
    ONBOARDING_KEYS_2_0, ONBOARDING_MULTI_KEYS_2_0,
    ensure_profile_answers_2_0, onboarding_presets, empty_profile_answer,
)
from tools.token_tools import CustomTokenTool, generate_login_user_token
from tools.utils import ApiResponse, resolve_platform_from_request, resolve_country_from_ip
from Apps.views.analytics.view import normalize_platform, touch_user_active, record_user_event

# update-profile 可写入 profile_answers 的顶层别名（Settings「About you」）
_PROFILE_ANSWER_TOP_KEYS = frozenset(k for k, _ in ONBOARDING_KEYS_2_0)

logger = logging.getLogger(__name__)


def _option_values_for_key(key):
    """从 onboarding_presets 取出某 key 的合法 value 集合。"""
    opts = onboarding_presets().get(key) or []
    return {
        str(o.get('value')).strip()
        for o in opts
        if isinstance(o, dict) and o.get('value') is not None and str(o.get('value')).strip()
    }


def _apply_profile_answers_patch(user, patch: dict):
    """合并并校验 2.0 扩展题答案到 user.profile_answers。非法返回错误文案。"""
    if not isinstance(patch, dict) or not patch:
        return None
    profile = dict(getattr(user, 'profile_answers', None) or {})
    for raw_key, raw_val in patch.items():
        key = str(raw_key).strip()
        if key not in _PROFILE_ANSWER_TOP_KEYS:
            continue
        allowed = _option_values_for_key(key)
        if key in ONBOARDING_MULTI_KEYS_2_0:
            if raw_val is None or raw_val == '' or raw_val == []:
                profile[key] = []
                continue
            if not isinstance(raw_val, list):
                return f'invalid {key}: expected list'
            cleaned = []
            for item in raw_val:
                v = str(item).strip()
                if not v:
                    continue
                if allowed and v not in allowed:
                    return f'invalid {key}: {v}'
                if v not in cleaned:
                    cleaned.append(v)
            profile[key] = cleaned
        else:
            if raw_val is None:
                profile[key] = empty_profile_answer(key)
                continue
            v = str(raw_val).strip()
            if not v:
                profile[key] = ''
                continue
            if allowed and v not in allowed:
                return f'invalid {key}: {v}'
            profile[key] = v
    user.profile_answers = ensure_profile_answers_2_0(profile)
    return None


@extend_schema(tags=[_('Selah认证')])
class AuthViewSet(ViewSet):
    """设备登录 / 三方登录 / 额度。

    权限：device_login、oauth_login 免鉴权；其余需 IsTokenValid。
    """

    permission_classes_by_action = {
        'device_login': [],
        'oauth_login': [],
        'bind_oauth': [],
        'me': [IsTokenValid],
        'quota': [IsTokenValid],
        'dismiss_bind_prompt': [IsTokenValid],
        'logout': [IsTokenValid],
        'update_profile': [IsTokenValid],
        'delete_account': [IsTokenValid],
        'cancel_delete': [IsTokenValid],
    }

    def get_throttle_scope(self):
        if getattr(self, 'action', None) in ('device_login', 'oauth_login'):
            return 'device_login'
        return 'user'

    def get_permissions(self):
        return [p() for p in self.permission_classes_by_action.get(self.action, [IsTokenValid])]

    @extend_schema(request=DeviceLoginSerializer, summary=_('设备ID快速登录'))
    @action(detail=False, methods=['post'], url_path='device-login')
    def device_login(self, request):
        """设备 ID 快速登录/注册；发放新人额度并返回 token。

        业务说明:
            - 账号身份是 (device_id, app_key)：同一物理设备进 Selah 与 Manna 是两个 uid。
            - 新用户：先 set_config_context(请求 app/语言/平台)，再 create，保证 streak_heart_initial 等取对维度。
            - 老用户：仅当 device_id 且 app_key 都命中时刷新 timezone/system_lang；platform 变更才写入。
            - 同 device_id、不同 app_key：新建用户，不 409、不覆盖对方 app_key。
            - grant_newcomer_bonus 幂等（已发过则 bonus=0）。
            - touch_user_active 刷新活跃日，供 DAU。
        """
        ensure_default_configs()
        ser = DeviceLoginSerializer(data=request.data)
        if not ser.is_valid():
            return ApiResponse(message=str(ser.errors), code=400)
        data = ser.validated_data
        device_id = data['device_id'].strip()
        plat = resolve_platform_from_request(request) or normalize_platform(data.get('platform') or '')
        from tools.selah_quota import normalize_app_key
        app_key = normalize_app_key(data.get('app_key') or '')
        set_config_context(data.get('system_lang') or 'en', plat, app_key)
        if not config_as_bool('device_login_enabled', True):
            return ApiResponse(message=_('Device login disabled'), code=403)
        is_new = False
        with transaction.atomic():
            user = User.objects.filter(device_id=device_id, app_key=app_key, is_deleted=False).first()
            if not user:
                is_new = True
                # username 仅作后台检索；真正主键身份是 (device_id, app_key)
                username = f'device_{device_id[:8]}_{uuid.uuid4().hex[:6]}'
                user = User.objects.create(
                    username=username,
                    device_id=device_id,
                    login_type='device',
                    role='user',
                    timezone=data.get('timezone') or 'UTC',
                    register_country=data.get('register_country') or resolve_country_from_ip(request),
                    system_lang=data.get('system_lang') or 'en',
                    platform=plat,
                    app_key=app_key,
                    password='',
                    # 初始冻结爱心来自配置，默认 3
                    streak_hearts=int(get_config('streak_heart_initial', 3)),
                )
            else:
                # 本 App 老设备重登：更新本地化字段，不重置额度/streak，不改 app_key
                user.timezone = data.get('timezone') or user.timezone
                user.system_lang = data.get('system_lang') or user.system_lang
                update_fields = ['timezone', 'system_lang', 'updated_at']
                if plat and user.platform != plat:
                    user.platform = plat
                    update_fields.append('platform')
                user.save(update_fields=update_fields)
            set_config_context_from_user(user)
            user, bonus = grant_newcomer_bonus(user)
            # 提审临时：快捷登录默认开通 VIP；测支付期间默认关（配置/默认均为 False）
            if config_as_bool('review_device_login_as_vip', False) and not user.is_vip_active:
                from Apps.views.membership.view import activate_vip
                user = activate_vip(user, days=365, is_trial=True, product_id='review_device_login')
            if is_new:
                # 新用户：埋点内部自动执行 touch_user_active，不用外层再调用
                record_user_event(user, 'sign_up', {'source': 'device_login'})
            else:
                # 老用户：没有 sign_up，需手动刷活跃
                active_plat = plat or getattr(user, 'platform', '')
                touch_user_active(user, active_plat)
        if getattr(user, 'status', 1) == 0:
            return ApiResponse(message=_('账号已禁用'), code=403)
        token = generate_login_user_token(request, user, remember=True)
        resp = ApiResponse(
            data={
                'token': token,
                'is_new_user': is_new,
                'newcomer_bonus': bonus,
                'user': user_public_dict(user),
            },
            message=_('登录成功'),
        )
        persist_auth(request, resp, token, user.id, remember=True)
        logger.info('device_login user=%s new=%s', user.id, is_new)
        return resp

    def _issue_login(self, request, user, *, is_new=False, bonus=0, message=None, extra=None):
        """签发 token 并写 Cookie；统一 oauth/device 登录响应形状。"""
        if getattr(user, 'status', 1) == 0:
            return ApiResponse(message=_('账号已禁用'), code=403)
        token = generate_login_user_token(request, user, remember=True)
        data = {
            'token': token,
            'is_new_user': is_new,
            'newcomer_bonus': bonus,
            'user': user_public_dict(user),
        }
        if extra:
            data.update(extra)
        resp = ApiResponse(data=data, message=message or _('登录成功'))
        persist_auth(request, resp, token, user.id, remember=True)
        return resp

    @extend_schema(request=OAuthLoginSerializer, summary=_('Google/Apple 登录（独立账号）'))
    @action(detail=False, methods=['post'], url_path='oauth-login')
    def oauth_login(self, request):
        """三方登录：按 oauth_id 找/建独立账号并发 token（与设备账号不合并）。

        业务说明:
            - 免鉴权；登录页或设备会话内均可调用，成功后客户端换 token = 切换到三方账号。
            - 只按 (google_id|apple_id, app_key) 找或建用户；不写设备号、不抢 device_id、不并入 device 用户。
            - device_id 可选，仅日志，不参与身份。
            - Google：oauth_id 必填（信客户端 openId）；Apple：生产验 id_token，oauth_id 可省略（用 token.sub）。
        """
        ensure_default_configs()
        ser = OAuthLoginSerializer(data=request.data)
        if not ser.is_valid():
            return ApiResponse(message=str(ser.errors), code=400)
        data = ser.validated_data
        from tools.selah_quota import normalize_app_key as _norm_app, set_config_context
        plat_early = resolve_platform_from_request(request) or normalize_platform(data.get('platform') or '')
        set_config_context(data.get('system_lang') or 'en', plat_early, _norm_app(data.get('app_key') or ''))
        if not config_as_bool('oauth_login_enabled', True):
            return ApiResponse(message=_('OAuth login disabled'), code=403)
        provider = data['provider']
        oauth_id = (data.get('oauth_id') or '').strip()
        device_id = (data.get('device_id') or '').strip()
        from tools.oauth_verify import verify_oauth_identity
        ok, sub, msg = verify_oauth_identity(provider, oauth_id, data.get('id_token') or '')
        if not ok:
            return ApiResponse(message=msg or 'oauth verify failed', code=400)
        oauth_id = (sub or oauth_id).strip()
        if not oauth_id:
            return ApiResponse(message='oauth_id required', code=400)
        field = 'google_id' if provider == 'google' else 'apple_id'
        plat = resolve_platform_from_request(request) or normalize_platform(data.get('platform') or '')
        from tools.selah_quota import normalize_app_key
        app_key = normalize_app_key(data.get('app_key') or '')
        system_lang = data.get('system_lang') or 'en'

        with transaction.atomic():
            user = User.objects.filter(**{field: oauth_id, 'app_key': app_key, 'is_deleted': False}).first()
            is_new = False
            if user:
                existing_app = normalize_app_key(getattr(user, 'app_key', '') or '')
                if app_key and existing_app and existing_app != app_key:
                    return ApiResponse(
                        message=_('该三方账号已绑定其他应用'),
                        code=409,
                    )
                user.login_type = provider
                if data.get('display_name'):
                    user.display_name = data['display_name']
                if data.get('email') and not user.email:
                    user.email = data['email']
                user.timezone = data.get('timezone') or user.timezone
                user.system_lang = system_lang or user.system_lang
                update_fields = [
                    'login_type', 'timezone', 'system_lang', 'updated_at',
                    'display_name', 'email',
                ]
                if plat and user.platform != plat:
                    user.platform = plat
                    update_fields.append('platform')
                if app_key and not existing_app:
                    user.app_key = app_key
                    update_fields.append('app_key')
                user.save(update_fields=update_fields)
            else:
                is_new = True
                set_config_context(system_lang, plat, app_key)
                username = f'{provider}_{oauth_id[:8]}_{uuid.uuid4().hex[:6]}'
                create_kwargs = {
                    'username': username,
                    'device_id': None,
                    'login_type': provider,
                    'role': 'user',
                    'timezone': data.get('timezone') or 'UTC',
                    'register_country': data.get('register_country') or resolve_country_from_ip(request),
                    'system_lang': system_lang,
                    'platform': plat,
                    'app_key': app_key,
                    'password': '',
                    'streak_hearts': int(get_config('streak_heart_initial', 3)),
                    field: oauth_id,
                }
                if data.get('email'):
                    create_kwargs['email'] = data['email']
                if data.get('display_name'):
                    create_kwargs['display_name'] = data['display_name']
                user = User.objects.create(**create_kwargs)

            set_config_context_from_user(user)
            user, bonus = grant_newcomer_bonus(user)
            # 与快捷登录同一开关：提审期间 Google/Apple 也可开通 VIP；测支付期间默认关
            if config_as_bool('review_device_login_as_vip', False) and not user.is_vip_active:
                from Apps.views.membership.view import activate_vip
                user = activate_vip(user, days=365, is_trial=True, product_id='review_oauth_login')
            if is_new:
                record_user_event(user, 'sign_up', {'source': 'oauth'})
            else:
                active_plat = plat or getattr(user, 'platform', '')
                touch_user_active(user, active_plat)

        logger.info(
            'oauth_login user=%s new=%s provider=%s device_hint=%s',
            user.id, is_new, provider, (device_id[:12] + '…') if len(device_id) > 12 else device_id,
        )
        return self._issue_login(request, user, is_new=is_new, bonus=bonus)

    @extend_schema(request=OAuthBindSerializer, summary=_('绑定 Google/Apple（已废弃）'))
    @action(detail=False, methods=['post'], url_path='bind-oauth')
    def bind_oauth(self, request):
        """已废弃：设备账号与三方账号互不合并，请改用 oauth-login 切换到三方账号。"""
        return ApiResponse(
            message=_('绑定已废弃：请使用 oauth-login 切换到 Google/Apple 账号，与设备账号相互独立'),
            code=410,
        )

    @extend_schema(summary=_('当前用户'))
    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        """当前用户公开信息；顺带刷新活跃时间。

        业务说明:
            platform 查询参数可顺带纠正用户平台字段（经 touch_user_active）。
        """
        plat = normalize_platform(request.query_params.get('platform') or '')
        touch_user_active(request.user, plat)
        return ApiResponse(data=user_public_dict(request.user))

    @extend_schema(summary=_('额度查询'))
    @action(detail=False, methods=['get'], url_path='quota')
    def quota(self, request):
        """查询 Free/VIP AI 剩余额度。

        业务说明:
            Free 看 free_quota；VIP 看当日 vip_daily_remaining（含跨日刷新逻辑在 get_remaining_quota）。
        """
        from tools.selah_quota import get_remaining_quota
        return ApiResponse(data=get_remaining_quota(request.user))

    @extend_schema(summary=_('关闭绑定弱提示'))
    @action(detail=False, methods=['post'], url_path='dismiss-bind-prompt')
    def dismiss_bind_prompt(self, request):
        """关闭绑定弱提示。

        业务说明:
            客户端「稍后再说」后置 bind_prompt_dismissed，不再弹绑定引导。
        """
        u = request.user
        u.bind_prompt_dismissed = True
        u.save(update_fields=['bind_prompt_dismissed', 'updated_at'])
        return ApiResponse(message=_('已关闭'))

    @extend_schema(summary=_('更新资料/设置'))
    @action(detail=False, methods=['post'], url_path='update-profile')
    def update_profile(self, request):
        """更新资料与设置项。

        业务说明:
            Serializer 白名单列字段 + timezone/wallpaper_id 兼容直写。
            emotion_status / denomination_pref / 其它 2.0 key / profile_answers
            合并进 profile_answers（校验枚举；空值可清空）。
        """
        ser = UpdateProfileSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        u = request.user
        data = dict(ser.validated_data)
        answers_patch = data.pop('profile_answers', None)
        if not isinstance(answers_patch, dict):
            answers_patch = {}
        for key in list(data.keys()):
            if key in _PROFILE_ANSWER_TOP_KEYS:
                answers_patch[key] = data.pop(key)

        for k, v in data.items():
            setattr(u, k, v)
        # 兼容未列入 serializer 的可选字段
        for k in ('timezone', 'wallpaper_id'):
            if k in request.data:
                setattr(u, k, request.data[k])

        if answers_patch:
            err = _apply_profile_answers_patch(u, answers_patch)
            if err:
                return ApiResponse(message=err, code=400)

        u.save()
        return ApiResponse(data=user_public_dict(u), message=_('已更新'))

    @extend_schema(summary=_('登出'))
    @action(detail=False, methods=['post'], url_path='logout')
    def logout(self, request):
        """删除 token 并清理会话 Cookie。

        业务说明:
            设备账号本身不删除；仅失效当前 token，下次仍可用 device_id 登录。
        """
        token = get_token_from_request(request)
        if token:
            CustomTokenTool.delete_token(token)
        resp = ApiResponse(message=_('登出成功'))
        return clear_auth(request, resp)

    @extend_schema(summary=_('申请注销账号（7天冷静期）'))
    @action(detail=False, methods=['post'], url_path='delete-account')
    def delete_account(self, request):
        """申请注销：进入 7 天冷静期，踢全部会话。

        业务说明:
            - 冷静期内仍可登录并用 App；登录/me 带 deletion_pending 供弹提示。
            - 点「保留账号」调 cancel-delete；满 7 天未撤销由定时任务软删。
            - 与 logout 不同：本接口会排程注销；logout 只失效 token。
        """
        try:
            pending = request_account_deletion(request.user)
        except ValueError as e:
            code = str(e)
            if code == 'deletion_already_pending':
                return ApiResponse(message=_('已在注销冷静期内'), code=400)
            if code == 'admin_cannot_self_delete':
                return ApiResponse(message=_('管理员账号请走后台处理'), code=403)
            if code == 'account_already_deleted':
                return ApiResponse(message=_('账号已注销'), code=401)
            return ApiResponse(message=_('无法申请注销'), code=400)

        try:
            record_user_event(request.user, 'account_deletion_requested', {})
        except Exception:
            logger.exception('record deletion_requested event failed user=%s', request.user.id)

        resp = ApiResponse(
            data=pending,
            message=_('已申请注销，7 天内登录可撤销'),
        )
        return clear_auth(request, resp)

    @extend_schema(summary=_('撤销注销（冷静期内）'))
    @action(detail=False, methods=['post'], url_path='cancel-delete')
    def cancel_delete(self, request):
        """冷静期内撤销注销申请。"""
        try:
            cancel_account_deletion(request.user)
        except ValueError as e:
            code = str(e)
            if code == 'no_pending_deletion':
                return ApiResponse(message=_('当前不在注销冷静期'), code=400)
            if code == 'account_already_deleted':
                return ApiResponse(message=_('账号已注销'), code=401)
            return ApiResponse(message=_('无法撤销注销'), code=400)

        try:
            record_user_event(request.user, 'account_deletion_cancelled', {})
        except Exception:
            logger.exception('record deletion_cancelled event failed user=%s', request.user.id)

        return ApiResponse(
            data=user_public_dict(request.user),
            message=_('已撤销注销，账号恢复正常'),
        )
