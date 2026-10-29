#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""OAuth 身份校验：Google 信客户端 openId；Apple 验 id_token（JWKS + aud 白名单）。

沙盒 oauth_id（google_sandbox_* / apple_sandbox_*）仅 DEBUG 或 ALLOW_OAUTH_UNVERIFIED 放行。
"""
from __future__ import annotations

import logging
import os

import jwt
from django.conf import settings
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

APPLE_KEYS = 'https://appleid.apple.com/auth/keys'

_apple_jwks = None


def oauth_unverified_allowed():
    if os.getenv('ALLOW_OAUTH_UNVERIFIED', '').lower() in ('1', 'true', 'yes'):
        return True
    return bool(getattr(settings, 'DEBUG', False))


def apple_audiences():
    """identityToken 允许的 aud：APPLE_OAUTH_CLIENT_ID + APPLE_OAUTH_CLIENT_IDS（逗号分隔）。"""
    ids = []
    single = (os.getenv('APPLE_OAUTH_CLIENT_ID') or '').strip()
    if single:
        ids.append(single)
    multi = os.getenv('APPLE_OAUTH_CLIENT_IDS') or ''
    ids.extend(x.strip() for x in multi.split(',') if x.strip())
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def verify_oauth_identity(provider, oauth_id, id_token=''):
    """返回 (ok, resolved_sub, message)。resolved_sub 应写入 google_id/apple_id。

    Google：信任客户端上报的 oauth_id（openId），忽略 id_token。
    Apple：生产须验 id_token（JWKS + aud 白名单）；无 token 时仅 DEBUG / ALLOW_OAUTH_UNVERIFIED 放行。
    """
    provider = (provider or '').strip().lower()
    oauth_id = (oauth_id or '').strip()
    id_token = (id_token or '').strip()
    sandbox_prefix = 'google_sandbox_' if provider == 'google' else 'apple_sandbox_'
    if oauth_id.startswith(sandbox_prefix) or oauth_id.startswith(f'{provider}_sandbox_'):
        if oauth_unverified_allowed():
            return True, oauth_id, 'sandbox'
        return False, '', 'sandbox oauth disabled'

    if provider == 'google':
        if not oauth_id:
            return False, '', 'oauth_id required'
        return True, oauth_id, 'google-openid'

    if not id_token:
        if oauth_unverified_allowed():
            logger.warning('oauth_verify: accepting unverified oauth_id provider=%s', provider)
            return True, oauth_id, 'unverified'
        return False, '', 'id_token required'

    if provider == 'apple':
        return _verify_apple(oauth_id, id_token)
    return False, '', 'unknown provider'


def _apple_jwk_client():
    global _apple_jwks
    if _apple_jwks is None:
        _apple_jwks = PyJWKClient(APPLE_KEYS)
    return _apple_jwks


def _verify_apple(oauth_id, id_token):
    """JWKS + RS256/ES256 验签；核对 iss / aud 白名单 / sub。"""
    audiences = apple_audiences()
    try:
        signing_key = _apple_jwk_client().get_signing_key_from_jwt(id_token)
        payload = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=['RS256', 'ES256'],
            audience=audiences or None,
            issuer='https://appleid.apple.com',
            options={
                'verify_aud': bool(audiences),
                'verify_iss': True,
                'verify_exp': True,
            },
            leeway=30,
        )
    except jwt.PyJWTError as e:
        logger.warning('apple id_token verify failed: %s', e)
        return False, '', 'apple token verify failed'
    except Exception as e:
        logger.exception('apple jwks verify failed: %s', e)
        return False, '', 'apple token verify failed'
    sub = str(payload.get('sub') or '').strip()
    if not sub:
        return False, '', 'apple sub missing'
    if oauth_id and oauth_id != sub:
        return False, '', 'oauth_id mismatch'
    if not audiences:
        logger.warning(
            'oauth_verify: APPLE_OAUTH_CLIENT_ID(S) unset; aud not checked'
        )
    return True, sub, 'apple'
