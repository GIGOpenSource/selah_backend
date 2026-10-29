#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""IAP 验签：Apple verifyReceipt / Server API，Google Play Developer API。

- 开发：允许 sandbox（受 DEBUG + AppConfig.iap_sandbox_allowed）。
- 生产：默认拒绝 sandbox；无商店凭证默认拒绝（ALLOW_IAP_UNVERIFIED=1 仅联调）。
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt
from django.conf import settings

from tools.selah_quota import get_config

logger = logging.getLogger(__name__)

APPLE_VERIFY_PROD = 'https://buy.itunes.apple.com/verifyReceipt'
APPLE_VERIFY_SANDBOX = 'https://sandbox.itunes.apple.com/verifyReceipt'
APPLE_STOREKIT_PROD = 'https://api.storekit.itunes.apple.com'
APPLE_STOREKIT_SANDBOX = 'https://api.storekit-sandbox.itunes.apple.com'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_PUBLISHER = 'https://androidpublisher.googleapis.com/androidpublisher/v3/applications'
PLAY_SCOPE = 'https://www.googleapis.com/auth/androidpublisher'

_google_token_cache = {'token': '', 'exp': 0}


def is_production() -> bool:
    """DEBUG=False 视为生产（含 pro 设置模块）。"""
    return not bool(getattr(settings, 'DEBUG', False))


def sandbox_allowed() -> bool:
    """
    是否允许客户端传 sandbox=true 旁路发权。

    规则:
    1. 生产环境：仅当环境变量 ALLOW_IAP_SANDBOX=1 且配置 iap_sandbox_allowed=true 时放行。
    2. 开发环境：默认放行；运营可把 iap_sandbox_allowed 设为 false 强制关。
    """
    cfg_ok = bool(get_config('iap_sandbox_allowed', True))
    if is_production():
        env_ok = os.getenv('ALLOW_IAP_SANDBOX', '').lower() in ('1', 'true', 'yes')
        return env_ok and cfg_ok
    return cfg_ok


def _allow_unverified() -> bool:
    """无商店凭证时是否仍接受非空收据（仅联调）。生产须环境变量与运营开关同时打开。"""
    env_ok = os.getenv('ALLOW_IAP_UNVERIFIED', '').lower() in ('1', 'true', 'yes')
    try:
        cfg_ok = bool(get_config('iap_unverified_allowed', False))
    except Exception:
        cfg_ok = False
    if is_production():
        return env_ok and cfg_ok
    return env_ok or cfg_ok


def _read_pem_or_json(raw: str):
    """环境变量：文件路径、JSON 对象、或 PEM（\\n 转义）。"""
    text = (raw or '').strip()
    if not text:
        return ''
    if os.path.isfile(text):
        with open(text, encoding='utf-8') as f:
            return f.read()
    return text.replace('\\n', '\n')


def google_play_package(app_key: str = '') -> str:
    """按 App 取 Play 包名。未配专属包名时回落 GOOGLE_PLAY_PACKAGE。"""
    app = (app_key or '').strip().lower()
    specific = ''
    if app == 'manna':
        specific = (os.getenv('GOOGLE_PLAY_PACKAGE_MANNA') or '').strip()
    elif app in ('', 'selah'):
        specific = (os.getenv('GOOGLE_PLAY_PACKAGE_SELAH') or '').strip()
    if specific:
        return specific
    return (os.getenv('GOOGLE_PLAY_PACKAGE') or 'com.example.app').strip()


def verify_store_receipt(*, platform: str, product_id: str, transaction_id: str, receipt_data: str, sandbox: bool, app_key: str = ''):
    """
    商店收据校验挂钩。

    Returns:
        (ok: bool, message: str, meta: dict)
    """
    if sandbox:
        if not sandbox_allowed():
            return False, 'sandbox IAP disabled in this environment', {}
        return True, 'sandbox', {'mode': 'sandbox'}

    if not (receipt_data or '').strip():
        return False, 'receipt_data required', {}

    apple_secret = (os.getenv('APPLE_SHARED_SECRET') or '').strip()
    apple_issuer = (os.getenv('APPLE_IAP_ISSUER_ID') or '').strip()
    apple_key_id = (os.getenv('APPLE_IAP_KEY_ID') or '').strip()
    apple_pem = _read_pem_or_json(os.getenv('APPLE_IAP_PRIVATE_KEY') or '')
    google_key = (os.getenv('GOOGLE_PLAY_SERVICE_ACCOUNT_JSON') or '').strip()

    plat = (platform or '').strip().lower()
    if plat == 'ios':
        if apple_issuer and apple_key_id and apple_pem:
            return _verify_apple_server_api(product_id, transaction_id, receipt_data)
        if apple_secret:
            return _verify_apple_shared_secret(product_id, transaction_id, receipt_data, apple_secret)
    if plat == 'android' and google_key:
        return _verify_google(product_id, transaction_id, receipt_data, app_key=app_key)

    if is_production() and not _allow_unverified():
        logger.error(
            'iap_verify: no store credentials; rejecting in production platform=%s product=%s',
            platform, product_id,
        )
        return False, 'store verification credentials not configured', {
            'mode': 'missing_credentials',
        }

    logger.warning(
        'iap_verify: no store credentials; accepting non-empty receipt platform=%s product=%s tx=%s',
        platform, product_id, transaction_id,
    )
    return True, 'receipt_present', {'mode': 'receipt_required'}


def _post_apple_verify(url: str, body: dict) -> dict:
    from tools.http_retry import http_json
    _, data = http_json(url, method='POST', data=body, timeout=20, retries=2)
    return data if isinstance(data, dict) else {}


def _post_form(url: str, fields: dict, timeout=20) -> dict:
    body = urllib.parse.urlencode(fields).encode('utf-8')
    req = urllib.request.Request(
        url, data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8') or '{}')


def _verify_apple_shared_secret(product_id: str, transaction_id: str, receipt_data: str, secret: str):
    """Apple verifyReceipt（共享密钥）。生产失败码 21007 时回落沙箱 URL。"""
    body = {
        'receipt-data': receipt_data,
        'password': secret,
        'exclude-old-transactions': True,
    }
    try:
        data = _post_apple_verify(APPLE_VERIFY_PROD, body)
        status = int(data.get('status') or -1)
        if status == 21007:
            data = _post_apple_verify(APPLE_VERIFY_SANDBOX, body)
            status = int(data.get('status') or -1)
        if status != 0:
            logger.warning('iap_verify apple status=%s product=%s', status, product_id)
            return False, f'Apple verifyReceipt status={status}', {
                'mode': 'apple_verify_receipt', 'status': status,
            }
        return True, 'apple_verify_receipt', {
            'mode': 'apple_verify_receipt',
            'product_id': product_id,
            'transaction_id': transaction_id,
            'environment': data.get('environment') or '',
        }
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        logger.exception('iap_verify apple HTTP failed: %s', e)
        return False, 'Apple verifyReceipt request failed', {'mode': 'apple_verify_receipt'}


def _apple_storekit_jwt():
    issuer = (os.getenv('APPLE_IAP_ISSUER_ID') or '').strip()
    key_id = (os.getenv('APPLE_IAP_KEY_ID') or '').strip()
    pem = _read_pem_or_json(os.getenv('APPLE_IAP_PRIVATE_KEY') or '')
    bundle = (os.getenv('APPLE_IAP_BUNDLE_ID') or '').strip()
    if not (issuer and key_id and pem):
        return ''
    now = int(time.time())
    claims = {
        'iss': issuer,
        'iat': now,
        'exp': now + 1200,
        'aud': 'appstoreconnect-v1',
    }
    if bundle:
        claims['bid'] = bundle
    return jwt.encode(
        claims, pem, algorithm='ES256',
        headers={'alg': 'ES256', 'kid': key_id, 'typ': 'JWT'},
    )


def _decode_jws_payload(jws: str) -> dict:
    parts = (jws or '').split('.')
    if len(parts) < 2:
        return {}
    import base64
    seg = parts[1]
    pad = '=' * ((4 - len(seg) % 4) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(seg + pad).decode('utf-8'))
    except (ValueError, json.JSONDecodeError):
        return {}


def _verify_apple_server_api(product_id: str, transaction_id: str, receipt_data: str):
    """App Store Server API：JWT + GET /inApps/v1/transactions/{id}。失败再回落共享密钥。"""
    token = _apple_storekit_jwt()
    tx = (transaction_id or '').strip() or (receipt_data or '').strip()
    if token and tx:
        from tools.http_retry import http_json
        headers = {'Authorization': 'Bearer %s' % token, 'Accept': 'application/json'}
        last_err = None
        for base in (APPLE_STOREKIT_PROD, APPLE_STOREKIT_SANDBOX):
            url = '%s/inApps/v1/transactions/%s' % (base, urllib.parse.quote(tx, safe=''))
            try:
                status, data = http_json(url, headers=headers, timeout=20, retries=1)
            except Exception as e:
                last_err = e
                logger.warning('iap_verify apple server api %s: %s', base, e)
                continue
            if status == 404:
                continue
            signed = ''
            if isinstance(data, dict):
                signed = data.get('signedTransactionInfo') or data.get('signedRenewalInfo') or ''
            payload = _decode_jws_payload(signed) if signed else (data if isinstance(data, dict) else {})
            pid = str(payload.get('productId') or payload.get('product_id') or '')
            if product_id and pid and pid != product_id:
                return False, 'apple product mismatch', {'mode': 'apple_server_api'}
            env = 'Sandbox' if 'sandbox' in base else 'Production'
            return True, 'apple_server_api', {
                'mode': 'apple_server_api',
                'product_id': product_id,
                'transaction_id': tx,
                'environment': env,
            }
        if last_err:
            logger.warning('iap_verify apple server api exhausted: %s', last_err)
    secret = (os.getenv('APPLE_SHARED_SECRET') or '').strip()
    if secret:
        return _verify_apple_shared_secret(product_id, transaction_id, receipt_data, secret)
    logger.error('iap_verify: Apple Server API JWT failed and no APPLE_SHARED_SECRET')
    return False, 'Apple IAP Server API not configured', {
        'mode': 'store_api', 'product_id': product_id, 'transaction_id': transaction_id,
    }


def _google_play_access_token():
    now = time.time()
    if _google_token_cache['token'] and _google_token_cache['exp'] > now + 60:
        return _google_token_cache['token']
    raw = (os.getenv('GOOGLE_PLAY_SERVICE_ACCOUNT_JSON') or '').strip()
    if not raw:
        return ''
    try:
        text = _read_pem_or_json(raw)
        info = json.loads(text) if text.lstrip().startswith('{') else None
        if info is None:
            logger.error('iap_verify: GOOGLE_PLAY_SERVICE_ACCOUNT_JSON is not JSON')
            return ''
        iat = int(now)
        assertion = jwt.encode(
            {
                'iss': info['client_email'],
                'scope': PLAY_SCOPE,
                'aud': GOOGLE_TOKEN_URL,
                'iat': iat,
                'exp': iat + 3600,
            },
            info['private_key'],
            algorithm='RS256',
        )
        data = _post_form(GOOGLE_TOKEN_URL, {
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': assertion,
        })
        access = str(data.get('access_token') or '')
        expires_in = int(data.get('expires_in') or 3600)
        _google_token_cache['token'] = access
        _google_token_cache['exp'] = now + max(60, expires_in - 30)
        return access
    except Exception as e:
        logger.exception('iap_verify google service-account token failed: %s', e)
        return ''


def _acknowledge_google_product(package: str, product_id: str, token: str, access: str):
    """一次性商品确认购买。已确认过则忽略，避免 3 天后被 Google 自动退款。"""
    from tools.http_retry import http_json
    enc_pkg = urllib.parse.quote(package, safe='')
    enc_pid = urllib.parse.quote(product_id, safe='')
    enc_tok = urllib.parse.quote(token, safe='')
    url = '%s/%s/purchases/products/%s/tokens/%s:acknowledge' % (
        GOOGLE_PUBLISHER, enc_pkg, enc_pid, enc_tok,
    )
    try:
        http_json(
            url, method='POST', data={},
            headers={'Authorization': 'Bearer %s' % access},
            timeout=20, retries=1,
        )
    except Exception as e:
        logger.warning('iap_verify google acknowledge skipped: %s', e)


def _verify_google(product_id: str, transaction_id: str, receipt_data: str, app_key: str = ''):
    """Google Play Developer API：服务账号换 token 后查 products / subscriptions。"""
    package = google_play_package(app_key)
    access = _google_play_access_token()
    token = (receipt_data or '').strip()
    if not access or not token or not package:
        logger.error('iap_verify: Google Play token/package/credentials missing app=%s', app_key)
        return False, 'Google Play verification not configured', {
            'mode': 'store_api', 'product_id': product_id, 'transaction_id': transaction_id,
        }
    from tools.http_retry import http_json
    enc_pkg = urllib.parse.quote(package, safe='')
    enc_pid = urllib.parse.quote(product_id, safe='')
    enc_tok = urllib.parse.quote(token, safe='')
    headers = {'Authorization': 'Bearer %s' % access}
    urls = [
        ('product', '%s/%s/purchases/products/%s/tokens/%s' % (GOOGLE_PUBLISHER, enc_pkg, enc_pid, enc_tok)),
        ('subscription', '%s/%s/purchases/subscriptions/%s/tokens/%s' % (GOOGLE_PUBLISHER, enc_pkg, enc_pid, enc_tok)),
    ]
    last_payload = {}
    for kind, url in urls:
        try:
            _, data = http_json(url, headers=headers, timeout=20, retries=2)
        except Exception as e:
            logger.warning('iap_verify google HTTP %s: %s', url, e)
            continue
        last_payload = data if isinstance(data, dict) else {}
        state = last_payload.get('purchaseState')
        payment = last_payload.get('paymentState')
        expiry = last_payload.get('expiryTimeMillis')
        purchased = state in (0, '0') or payment in (1, '1', 2, '2')
        if expiry:
            try:
                purchased = purchased or int(expiry) > int(time.time() * 1000)
            except (TypeError, ValueError):
                pass
        if not purchased and (last_payload.get('error') or last_payload.get('error_description')):
            continue
        if not purchased:
            continue
        order = str(last_payload.get('orderId') or '')
        if transaction_id and order and transaction_id not in order and order not in transaction_id:
            logger.warning(
                'iap_verify google order id differs tx=%s order=%s; accepting verified token',
                transaction_id, order,
            )
        if kind == 'product' and str(last_payload.get('acknowledgementState')) not in ('1',):
            _acknowledge_google_product(package, product_id, token, access)
        return True, 'google_play', {
            'mode': 'google_play',
            'kind': kind,
            'package': package,
            'product_id': product_id,
            'transaction_id': transaction_id,
            'order_id': order,
        }
    return False, 'google purchase not valid', {'mode': 'google_play', 'payload': last_payload}


def parse_google_rtdn_push(body: dict) -> dict:
    """解开 Pub/Sub 推送里的 DeveloperNotification。不是这种包体则返回空。"""
    import base64
    message = body.get('message') if isinstance(body, dict) else None
    if not isinstance(message, dict):
        return {}
    raw = message.get('data') or ''
    if not raw:
        return {}
    try:
        decoded = base64.b64decode(raw)
        note = json.loads(decoded.decode('utf-8'))
    except (ValueError, json.JSONDecodeError):
        logger.warning('google rtdn: bad pubsub payload')
        return {}
    return note if isinstance(note, dict) else {}


def fetch_google_subscription_v2(package: str, purchase_token: str) -> dict:
    """用 purchaseToken 向 Play 查订阅。失败抛异常，方便通知重试。"""
    access = _google_play_access_token()
    if not access or not package or not purchase_token:
        raise RuntimeError('Google Play credentials or token missing')
    from tools.http_retry import http_json
    url = '%s/%s/purchases/subscriptionsv2/tokens/%s' % (
        GOOGLE_PUBLISHER,
        urllib.parse.quote(package, safe=''),
        urllib.parse.quote(purchase_token, safe=''),
    )
    _, data = http_json(
        url,
        headers={'Authorization': 'Bearer %s' % access},
        timeout=20,
        retries=2,
    )
    return data if isinstance(data, dict) else {}


def acknowledge_google_subscription(package: str, product_id: str, purchase_token: str):
    """确认订阅，避免 3 天后被 Google 自动退款。已确认过则忽略。"""
    access = _google_play_access_token()
    if not access:
        return
    from tools.http_retry import http_json
    url = '%s/%s/purchases/subscriptions/%s/tokens/%s:acknowledge' % (
        GOOGLE_PUBLISHER,
        urllib.parse.quote(package, safe=''),
        urllib.parse.quote(product_id, safe=''),
        urllib.parse.quote(purchase_token, safe=''),
    )
    try:
        http_json(
            url, method='POST', data={},
            headers={'Authorization': 'Bearer %s' % access},
            timeout=20, retries=1,
        )
    except Exception as e:
        logger.warning('google rtdn acknowledge skipped: %s', e)


def user_id_from_apple_account_token(token: str):
    """苹果 appAccountToken 必须是 UUID。这里用用户 ID 的整数形式，可还原。"""
    import uuid
    try:
        value = uuid.UUID(str(token or '').strip()).int
    except (ValueError, AttributeError, TypeError):
        return None
    if value <= 0:
        return None
    return value


def _apple_root_cert():
    from cryptography import x509
    path = os.path.join(os.path.dirname(__file__), 'certs', 'AppleRootCA-G3.cer')
    with open(path, 'rb') as handle:
        return x509.load_der_x509_certificate(handle.read())


def decode_apple_signed_payload(jws: str) -> dict:
    """校验苹果签名的 JWS，证书链必须回到 Apple Root CA G3。"""
    import base64
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    if not jws or str(jws).count('.') < 2:
        raise ValueError('apple jws missing')
    header = jwt.get_unverified_header(jws)
    x5c = header.get('x5c') or []
    if not x5c:
        raise ValueError('apple jws missing certificate')
    certs = [x509.load_der_x509_certificate(base64.b64decode(item)) for item in x5c]
    chain = certs + [_apple_root_cert()]
    for child, issuer in zip(chain, chain[1:]):
        try:
            issuer.public_key().verify(
                child.signature,
                child.tbs_certificate_bytes,
                child.signature_algorithm_parameters,
            )
        except InvalidSignature as exc:
            raise ValueError('apple certificate chain invalid') from exc
    payload = jwt.decode(
        jws, certs[0].public_key(), algorithms=['ES256'],
        options={'verify_aud': False},
    )
    return payload if isinstance(payload, dict) else {}


def parse_apple_server_notification(body: dict) -> dict:
    """解开 App Store Server Notifications V2。签名不对则抛异常。"""
    signed = body.get('signedPayload') if isinstance(body, dict) else ''
    if not signed:
        return {}
    note = decode_apple_signed_payload(signed)
    data = note.get('data') if isinstance(note.get('data'), dict) else {}
    tx_jws = data.get('signedTransactionInfo') or ''
    renewal_jws = data.get('signedRenewalInfo') or ''
    note['_transaction'] = decode_apple_signed_payload(tx_jws) if tx_jws else {}
    note['_renewal'] = decode_apple_signed_payload(renewal_jws) if renewal_jws else {}
    return note
