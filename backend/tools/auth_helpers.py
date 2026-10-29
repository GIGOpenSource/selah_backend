#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
登录态 Cookie / Header 辅助。

- get_token_from_request：header token → Authorization Token/Bearer → Cookie auth_token
- persist_auth / clear_auth：登录写 Cookie、登出删 Cookie
- token_redis_expire：Redis TTL（记住我 30 天，否则 TOKEN_EXPIRE_HOURS）

优先级说明：显式 header 优先于 Cookie，避免浏览器旧 Cookie 覆盖新 header。
"""
from datetime import timedelta

from tools.token_tools import TOKEN_EXPIRE_HOURS

# Cookie 默认 7 天；勾选「记住我」30 天（与 CustomTokenTool.generate_token 的 Redis TTL 对齐）
SESSION_EXPIRE_SECONDS = int(timedelta(days=7).total_seconds())
REMEMBER_EXPIRE_SECONDS = int(timedelta(days=30).total_seconds())
COOKIE_NAME = 'auth_token'


def get_token_from_request(request) -> str | None:
    """
    从请求中提取 token，顺序：

    1. headers['token']          — App / 运营台主路径
    2. Authorization: Token xxx  — DRF 风格
    3. Authorization: Bearer xxx — 通用网关风格
    4. Cookie auth_token         — Web/Swagger 会话

    任一命中即 strip 返回；全无则 None。
    """
    token = request.headers.get('token')
    if token:
        return token.strip()

    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if auth_header.startswith('Token '):
        return auth_header[6:].strip()
    if auth_header.startswith('Bearer '):
        return auth_header[7:].strip()

    token = request.COOKIES.get(COOKIE_NAME)
    if token:
        return token.strip()

    return None


def persist_auth(request, response, token: str, user_id: int, remember: bool = False):
    """
    登录成功后写入 Cookie（httponly=False，SameSite=Lax）。

    httponly=False：前端 JS 可读（历史选择）；生产若仅服务端会话可改为 True。
    user_id 参数保留供调用方日志/扩展，当前不写入 Cookie。
    """
    try:
        from tools.selah_quota import session_ttl_seconds
        max_age = session_ttl_seconds(remember)
    except Exception:
        max_age = REMEMBER_EXPIRE_SECONDS if remember else SESSION_EXPIRE_SECONDS

    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=False,
        samesite='Lax',
        secure=False,
    )
    return response


def clear_auth(request, response):
    """登出时清除 Cookie。"""
    response.delete_cookie(COOKIE_NAME)
    return response


def token_redis_expire(remember: bool = False) -> int:
    """
    Redis 中 token 过期秒数建议值。

    记住我 → 30 天；否则 TOKEN_EXPIRE_HOURS（小时转秒）。
    注意：generate_token 内部目前用 7/30 天，与本函数「非记住我用小时」不完全一致，
    调用方若自行 setKey 应以本函数或 generate_token 一侧为准统一。
    """
    try:
        from tools.selah_quota import session_ttl_seconds
        return session_ttl_seconds(remember)
    except Exception:
        if remember:
            return REMEMBER_EXPIRE_SECONDS
        return int(timedelta(hours=TOKEN_EXPIRE_HOURS).total_seconds())
