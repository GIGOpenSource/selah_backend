#!/usr/bin/env python
# -*- coding: UTF-8 -*-
import redis
import uuid
from django.conf import settings

class CustomTokenTool:
    """
    Token 工具类：基于 Redis 存储，支持生成、验证、删除 Token，设置 3600 秒过期
    """
    # 初始化 Redis 连接（从配置或硬编码获取连接信息）
    _redis = redis.Redis(
        host='192.168.77.222',  # 你的 Redis 主机
        port=6378,               # 你的 Redis 端口
        db=0,                    # 使用默认数据库
        decode_responses=True    # 自动将返回值解码为字符串（避免 bytes 类型问题）
    )
    _EXPIRE_SECONDS = 3600     # Token 有效期：3600 秒（1 小时）

    @classmethod
    def generate_token(cls, user_id):
        """
        生成 Token 并存储到 Redis
        :param user_id: 用户 ID（用于关联 Token 和用户）
        :return: 生成的 Token 字符串
        """
        # 生成 UUID 作为 Token（保证唯一性）
        token = str(uuid.uuid4())
        # 存储格式：key=token, value=user_id，设置过期时间
        cls._redis.setex(
            name=token,
            time=cls._EXPIRE_SECONDS,
            value=str(user_id)  # 用户 ID 转为字符串存储
        )

        return token

    @classmethod
    def verify_token(cls, token):
        """
        验证 Token 有效性
        :param token: 待验证的 Token 字符串
        :return: (is_valid: bool, user_id: int or None) 元组
        """
        if not token:
            return False, None  # Token 为空，直接无效

        # 从 Redis 获取用户 ID
        user_id_str = cls._redis.get(token)
        if not user_id_str:
            return False, None  # Token 不存在或已过期

        # 验证成功后，延长 Token 有效期（滑动窗口机制：每次访问延长 1 小时）
        cls._redis.expire(token, 3600)

        try:
            return True, int(user_id_str)  # 转换为整数用户 ID
        except ValueError:
            return False, None  # 用户 ID 格式错误（理论上不会发生）

    @classmethod
    def delete_token(cls, token):
        """
        删除 Token（登出时使用）
        :param token: 要删除的 Token 字符串
        """
        if token:
            cls._redis.delete(token)

    @classmethod
    def delete_login_token(cls, token):
        """
        删除 Token（登出时使用）
        :param token: 要删除的 Token 字符串
        """
        if token:
            cls._redis.delete(token)