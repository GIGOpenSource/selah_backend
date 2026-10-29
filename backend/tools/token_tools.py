#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
Token / Redis 工具（Selah）。

- RedisTool：连接池封装 set/get/del/expire
- CustomTokenTool：AES 登录 Token（Login: 前缀）与房间 Token（ChatRoom:）
- generate_is_user_token / generate_login_user_token：签发入口（登录会删旧 token）

Redis Key 布局：
  Login:{base64(IV||密文)}     → value=user_id；TTL 默认 7 天（记住我 30 天）
  ChatRoom:RoomToken:{base64} → value=room_id；TTL = ROOM_TOKEN_EXPIRE_HOURS

AES：CBC + PKCS7；密文前 16 字节为随机 IV；密钥 TOKEN_AES_KEY 截/填至 16 字节。
注意：TOKEN_EXPIRE_HOURS（载荷内时间戳）与 Redis TTL（天级）不一致时，以 Redis 存在为准做校验。
"""
import os
import redis
import base64
import time
import logging
from datetime import timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes
from tools.tools import getEnvConfig

logger = logging.getLogger(__name__)

# -------------------------- 全局配置项 --------------------------
# AES 密钥：优先 TOKEN_AES_KEY（须 16 字节）；开发默认 selah 占位
_aes = (getEnvConfig("TOKEN_AES_KEY") or os.getenv("TOKEN_AES_KEY") or "selah-token-key1").encode("utf-8")
AES_SECRET_KEY = (_aes + b"0" * 16)[:16]
# 登录Token过期时长(小时)——写入密文载荷的时间戳用；实际会话以 Redis TTL 为准
TOKEN_EXPIRE_HOURS = 2
# 聊天房间Token过期时长(小时)，可单独配置
ROOM_TOKEN_EXPIRE_HOURS = 4
# AES 固定IV长度
AES_IV_LENGTH = 16

# Redis 命名空间（模拟文件夹，按业务隔离）
REDIS_LOGIN_PREFIX = "Login:"    # 登录令牌目录：完整 key = Login: + base64
REDIS_CHAT_PREFIX = "ChatRoom:" # 聊天房间令牌目录：ChatRoom:RoomToken:...
# 用户 → 当前 token 集合（挤下线用；禁止 KEYS Login:* 全库扫描）
REDIS_USER_TOKENS_PREFIX = "LoginUserTokens:"


class RedisTool(object):
    """Redis 连接池工具：setKey / getKey / delKey / expireKey。"""
    def __init__(self):
        # 优先 REDIS_*；兼容历史 REDIS_BUILDMART_*
        self.host = (
            getEnvConfig("REDIS_HOST")
            or getEnvConfig("REDIS_BUILDMART_HOST")
            or "localhost"
        )
        self.port = int(
            getEnvConfig("REDIS_PORT")
            or getEnvConfig("REDIS_BUILDMART_PORT")
            or 6389
        )
        self.password = (
            getEnvConfig("REDIS_PASSWORD")
            or getEnvConfig("REDIS_BUILDMART_PASSWORD")
            or ""
        )
        self.db = int(getEnvConfig("REDIS_DB", 0))
        self.ex = 3600  # setKey/expireKey 未传 ex 时的默认 TTL（秒）
        max_connections = getEnvConfig("REDIS_MAX_CONNECTIONS", 10)
        try:
            max_connections = int(max_connections)
            if max_connections <= 0:
                raise ValueError("max_connections 必须是正整数")
        except (ValueError, TypeError):
            raise ValueError("max_connections 必须是正整数")

        self.pool = redis.ConnectionPool(
            host=self.host,
            port=self.port,
            password=self.password,
            db=self.db,
            max_connections=max_connections
        )
        # decode_responses=True：get 返回 str，便于直接 int(user_id)
        self.client = redis.Redis(connection_pool=self.pool, decode_responses=True)

    def setKey(self, key, values, ex=None):
        """写入键值，支持自定义过期秒数；默认 self.ex=3600。"""
        expire = ex if ex else self.ex
        self.client.set(key, values, ex=expire)
        return True

    def getKey(self, key):
        """读取键；不存在返回 None。"""
        return self.client.get(key)

    def delKey(self, key):
        """删除键。"""
        self.client.delete(key)
        return True

    def expireKey(self, key, ex=None):
        """续期键的 TTL（滑动过期：每次校验成功会续）。"""
        expire = ex if ex else self.ex
        self.client.expire(key, expire)


_redis = RedisTool()


class CustomTokenTool:
    """登录 Token 与聊天房间 Token 的生成 / 校验 / 删除。"""
    @staticmethod
    def _aes_encrypt(data: bytes) -> tuple[bytes, bytes]:
        """AES-CBC 加密：返回 (密文, IV)；IV 每次随机，拼在 token 前部。"""
        iv = get_random_bytes(AES_IV_LENGTH)
        cipher = AES.new(AES_SECRET_KEY, AES.MODE_CBC, iv)
        encrypted_data = cipher.encrypt(pad(data, AES.block_size))
        return encrypted_data, iv

    @staticmethod
    def _aes_decrypt(encrypted_data: bytes, iv: bytes) -> bytes:
        """AES-CBC 解密（当前 verify 以 Redis 为准，解密路径预留）。"""
        cipher = AES.new(AES_SECRET_KEY, AES.MODE_CBC, iv)
        decrypted_data = unpad(cipher.decrypt(encrypted_data), AES.block_size)
        return decrypted_data

    # ====================== 登录 Token ======================
    @classmethod
    def generate_token(cls, user_id: int, remember: bool = False) -> str:
        """
        生成用户登录 Token（Login: 前缀），并写入 Redis。

        载荷明文：\"{user_id}:{expire_timestamp}\" → AES → IV||密文 → base64。
        Redis：key=完整 token，value=user_id；
        TTL：remember → 30 天，否则 7 天（与 Cookie 对齐，见 auth_helpers）。
        """
        expire_timestamp = int(time.time()) + int(timedelta(hours=TOKEN_EXPIRE_HOURS).total_seconds())
        token_core = f"{user_id}:{expire_timestamp}".encode("utf-8")
        encrypted_core, iv = cls._aes_encrypt(token_core)
        token_bytes = iv + encrypted_core

        raw_token = base64.b64encode(token_bytes).decode("utf-8")
        token = f"{REDIS_LOGIN_PREFIX}{raw_token}"
        try:
            from tools.selah_quota import session_ttl_seconds
            expire_seconds = session_ttl_seconds(remember)
        except Exception:
            expire_seconds = int(timedelta(days=30 if remember else 7).total_seconds())
        _redis.setKey(token, user_id, ex=expire_seconds)
        # 维护 user→token 索引，供挤下线 O(成员) 删除，避免 KEYS 扫全库
        try:
            index = f"{REDIS_USER_TOKENS_PREFIX}{int(user_id)}"
            _redis.client.sadd(index, token)
            _redis.client.expire(index, expire_seconds)
        except Exception:
            logger.exception('写入 LoginUserTokens 索引失败 user_id=%s', user_id)
        return token

    @classmethod
    def verify_token(cls, token: str) -> tuple[bool, int | None]:
        """
        校验登录 Token；有效则续期并返回 (True, user_id)。

        兼容：无 Login: 前缀的旧 token 会自动补前缀再查 Redis。
        校验成功默认用 RedisTool.ex（3600s）续期——注意与签发时 7/30 天 TTL 不同，
        长期活跃会被「缩短续到 1 小时」；若需滑动 7 天应改 expireKey 入参。
        """
        try:
            if not token:
                return False, None
            # 兼容旧 token（无 Login: 前缀）
            redis_key = token if token.startswith(REDIS_LOGIN_PREFIX) else f"{REDIS_LOGIN_PREFIX}{token}"
            user_id_str = _redis.getKey(redis_key)
            if not user_id_str:
                return False, None
            try:
                from tools.selah_quota import session_ttl_seconds
                _redis.expireKey(redis_key, ex=session_ttl_seconds(False))
            except Exception:
                _redis.expireKey(redis_key)
            return True, int(user_id_str)
        except Exception as e:
            logger.exception('Token 校验失败：%s', e)
            return False, None

    @classmethod
    def delete_token(cls, token):
        """删除登录 Token（登出），并失效进程缓存（auth_session.invalidate_token）。"""
        if not token:
            return
        redis_key = token if str(token).startswith(REDIS_LOGIN_PREFIX) else f"{REDIS_LOGIN_PREFIX}{token}"
        # 尽量从 user 索引摘掉，避免残留
        try:
            uid = _redis.getKey(redis_key)
            if uid:
                _redis.client.srem(f"{REDIS_USER_TOKENS_PREFIX}{uid}", redis_key)
        except Exception:
            pass
        _redis.delKey(redis_key)
        try:
            from tools.auth_session import invalidate_token
            invalidate_token(token)
        except Exception:
            pass

    @classmethod
    def delete_user_all_tokens(cls, user_id: int):
        """
        删除某用户全部登录 Token（挤下线），并失效进程缓存。

        优先读 LoginUserTokens:{user_id} 集合；无索引时不再 KEYS 扫全库
        （遗留 Login:* 靠 TTL 自然过期，避免登录阻塞 / Redis 卡死）。
        """
        try:
            index = f"{REDIS_USER_TOKENS_PREFIX}{int(user_id)}"
            keys = list(_redis.client.smembers(index) or [])
            for key in keys:
                _redis.delKey(key)
            _redis.delKey(index)
            try:
                from tools.auth_session import invalidate_user
                invalidate_user(user_id)
            except Exception:
                pass
            return True
        except Exception as e:
            logger.exception('删除旧token失败: %s', e)
            return False

    # ====================== 聊天房间 Token ======================
    @classmethod
    def generate_room_token(cls, room_id: str | int) -> str:
        """
        生成聊天房间 Token。
        :param room_id: 聊天室 ID/房间号
        :return: RoomToken:xxx 格式令牌（对外）；Redis 完整键为 ChatRoom:RoomToken:xxx
        """
        expire_timestamp = int(time.time()) + int(timedelta(hours=ROOM_TOKEN_EXPIRE_HOURS).total_seconds())
        # 数据格式：房间号:过期时间
        token_core = f"{room_id}:{expire_timestamp}".encode("utf-8")
        encrypted_core, iv = cls._aes_encrypt(token_core)
        token_bytes = iv + encrypted_core

        raw_token = base64.b64encode(token_bytes).decode("utf-8")
        room_token = f"RoomToken:{raw_token}"
        # Redis 键：ChatRoom:RoomToken:xxx
        redis_key = f"{REDIS_CHAT_PREFIX}{room_token}"
        # 存入房间ID，使用房间专属过期时间
        _redis.setKey(redis_key, str(room_id), ex=int(timedelta(hours=ROOM_TOKEN_EXPIRE_HOURS).total_seconds()))
        return room_token

    @classmethod
    def verify_room_token(cls, room_token: str) -> tuple[bool, str | None]:
        """
        校验聊天房间 Token。
        :param room_token: 前端传入的房间令牌（须以 RoomToken: 开头）
        :return: (是否有效, 房间号)
        """
        try:
            if not room_token or not room_token.startswith("RoomToken:"):
                return False, None
            redis_key = f"{REDIS_CHAT_PREFIX}{room_token}"
            room_id = _redis.getKey(redis_key)
            if not room_id:
                return False, None
            # 续期房间Token（滑动过期，时长 = ROOM_TOKEN_EXPIRE_HOURS）
            _redis.expireKey(redis_key, ex=int(timedelta(hours=ROOM_TOKEN_EXPIRE_HOURS).total_seconds()))
            return True, room_id
        except Exception as e:
            logger.exception('房间Token 校验失败：%s', e)
            return False, None

    @classmethod
    def delete_room_token(cls, room_token: str):
        """销毁房间 Token（关闭房间/退出聊天）。"""
        if room_token and room_token.startswith("RoomToken:"):
            redis_key = f"{REDIS_CHAT_PREFIX}{room_token}"
            _redis.delKey(redis_key)


def generate_is_user_token(request, user):
    """
    签发登录 token（Basic/Swagger 等）。
    当前 existing_token 复用分支未启用（existing_token=0），始终新发。
    """
    if request:
        existing_token = 0
        if existing_token:
            is_valid, user_id = CustomTokenTool.verify_token(existing_token)
            if is_valid and user_id == user.id:
                return existing_token
    token = CustomTokenTool.generate_token(user.id)
    return token

def generate_login_user_token(request, user, remember=False):
    """正式登录：先删该用户全部旧 token（单端挤下线），再生成新 token。"""
    # ====================== 登录 删旧token ======================
    CustomTokenTool.delete_user_all_tokens(user.id)

    # 然后生成新 token
    token = CustomTokenTool.generate_token(user.id, remember=remember)
    return token
