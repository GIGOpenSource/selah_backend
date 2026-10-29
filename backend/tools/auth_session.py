#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
进程内短缓存：token → User，避免每次请求 Redis + DB 双跳。

缓存条目：token → (expire_ts, user_id, User 实例)
TTL：AUTH_CACHE_TTL_SEC=45；登出/挤下线会主动 invalidate。
容量：超过 _MAX_ENTRIES 先清过期，再丢一半最早过期项。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from models.models import User
from tools.token_tools import CustomTokenTool, REDIS_LOGIN_PREFIX

# 短 TTL：登出后最多延迟这么久才对本地缓存生效（delete 会主动失效）
AUTH_CACHE_TTL_SEC = 45
_MAX_ENTRIES = 2048

_lock = threading.Lock()
# token -> (expire_ts, user_id, user)
_cache: dict[str, tuple[float, int, User]] = {}


def _norm_token(token: str | None) -> str | None:
    """去空白；空串视为无 token。"""
    if not token:
        return None
    t = str(token).strip()
    return t or None


def _redis_key(token: str) -> str:
    """与 Redis 存储键对齐：无 Login: 前缀则补上。"""
    return token if token.startswith(REDIS_LOGIN_PREFIX) else f"{REDIS_LOGIN_PREFIX}{token}"


def invalidate_token(token: str | None) -> None:
    """
    登出等场景：从进程缓存移除该 token 的所有键形态。

    同时清：原始串、补前缀、去前缀、再拼前缀，避免形态不一致残留。
    """
    t = _norm_token(token)
    if not t:
        return
    with _lock:
        _cache.pop(t, None)
        rk = _redis_key(t)
        _cache.pop(rk, None)
        raw = t[len(REDIS_LOGIN_PREFIX):] if t.startswith(REDIS_LOGIN_PREFIX) else t
        _cache.pop(raw, None)
        _cache.pop(f"{REDIS_LOGIN_PREFIX}{raw}", None)


def invalidate_user(user_id: int) -> None:
    """挤下线等：清除该 user_id 在进程缓存中的全部条目。"""
    with _lock:
        dead = [k for k, v in _cache.items() if v[1] == int(user_id)]
        for k in dead:
            del _cache[k]


def _prune_unlocked(now: float) -> None:
    """调用方须已持 _lock。容量未超则直接返回。"""
    if len(_cache) < _MAX_ENTRIES:
        return
    expired = [k for k, v in _cache.items() if v[0] <= now]
    for k in expired:
        del _cache[k]
    if len(_cache) >= _MAX_ENTRIES:
        # 仍过多：丢掉最早过期的一半
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: len(_cache) // 2]
        for k, _ in oldest:
            del _cache[k]


def _get_cached(token: str) -> Optional[User]:
    """查缓存；过期条目删除并返回 None；兼容无前缀/有前缀两种 key。"""
    now = time.time()
    with _lock:
        item = _cache.get(token)
        if not item:
            alt = _redis_key(token)
            item = _cache.get(alt)
        if not item:
            return None
        exp, _uid, user = item
        if exp <= now:
            _cache.pop(token, None)
            return None
        return user


def _set_cached(token: str, user: User) -> None:
    """写入缓存；同时以原始 token 与 Redis 形态键各存一份，指向同一 entry。"""
    now = time.time()
    with _lock:
        _prune_unlocked(now)
        entry = (now + AUTH_CACHE_TTL_SEC, int(user.id), user)
        _cache[token] = entry
        _cache[_redis_key(token)] = entry


def resolve_user_by_token(token: str | None) -> Optional[User]:
    """
    校验 token 并返回 User。命中进程缓存则跳过 Redis + User 查询。

    未命中流程：
    1. CustomTokenTool.verify_token（Redis）
    2. User.objects.get(id=...)
    3. status==0（禁用）视为无效
    4. 写入短缓存后返回
    """
    t = _norm_token(token)
    if not t:
        return None

    cached = _get_cached(t)
    if cached is not None:
        return cached

    is_valid, user_id = CustomTokenTool.verify_token(t)
    if not is_valid or not user_id:
        return None

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return None

    # status=0：账号禁用。不要 return None（会变成普通 401 过期）
    if getattr(user, 'status', 1) == 0:
        from rest_framework.exceptions import AuthenticationFailed
        raise AuthenticationFailed({'code': 403, 'message': '账号已禁用'})

    # 已软删：token 应已清；兜底拒绝
    if getattr(user, 'is_deleted', False):
        from rest_framework.exceptions import AuthenticationFailed
        raise AuthenticationFailed({'code': 401, 'message': '账号已注销'})

    _set_cached(t, user)
    return user


def is_resolved_user(user) -> bool:
    """是否已是业务 User（非 AnonymousUser / 无 id）。供 IsTokenValid 复用认证结果。"""
    if user is None:
        return False
    if getattr(user, 'is_anonymous', False):
        return False
    return getattr(user, 'id', None) is not None
