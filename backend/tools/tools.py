#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
@Project ：NoBad 
@File    ：tools.py
@Author  ：LYP
@Date    ：2025/10/30 13:36 
@description :
"""
import os
from enum import Enum
import logging
from pathlib import Path
from django.contrib.auth.hashers import make_password, check_password
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件（与 pro.py 保持一致）
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)
logger = logging.getLogger('info')
res = dict()


def encryptPassword(password):
    """
    密码加密
    :param password:
    :return:
    """
    return make_password(password, None, 'pbkdf2_sha256')


def checkPassword(password, hash_password):
    """
    密码校验
    :param password:
    :param hash_password:
    :return:
    """
    return check_password(password, hash_password)


def getEnvConfig(key: str, default=None):
    """
    获取环境变量
    :param default:
    :param key:
    :return:
    """
    return os.getenv(key, default)


class CustomStatus(Enum):
    """
    自定义状态枚举类
    """
    # 成功状态
    SUCCESS = (200, "成功")
    CREATED = (201, "创建成功")
    UPDATED = (202, "更新成功")
    DELETED = (204, "删除成功")

    # 客户端错误状态
    BAD_REQUEST = (400, "请求参数错误")
    UNAUTHORIZED = (401, "未授权访问")
    FORBIDDEN = (403, "禁止访问")
    NOT_FOUND = (404, "资源不存在")
    METHOD_NOT_ALLOWED = (405, "请求方法不允许")

    # 认证授权相关错误
    USERNAME_EXISTS = (1001, "用户名已存在")
    INVALID_CREDENTIALS = (1002, "用户名或密码错误")
    ACCOUNT_DISABLED = (1003, "账户已被禁用")
    MISSING_REQUIRED_FIELDS = (1004, "缺少必要字段")
    TOKEN_EXPIRED = (1005, "令牌已过期")
    TOKEN_INVALID = (1006, "令牌无效")
    PERMISSION_DENIED = (1007, "权限不足")
    CREDENTIALS_EMPTY = (1008, "用户名或密码不能为空")
    USER_NOT_FOUND = (1009, "用户不存在")

    # 业务逻辑错误状态
    PRODUCT_NOT_AVAILABLE = (2001, "商品不可用")
    INSUFFICIENT_STOCK = (2002, "库存不足")
    ORDER_ALREADY_PAID = (2003, "订单已支付")
    PAYMENT_FAILED = (2004, "支付失败")
    INVALID_OPERATION = (2005, "无效操作")

    # 数据验证错误
    VALIDATION_ERROR = (3001, "数据验证失败")
    INVALID_FORMAT = (3002, "数据格式不正确")
    OUT_OF_RANGE = (3003, "数值超出范围")

    # 服务器错误状态
    INTERNAL_ERROR = (500, "服务器内部错误")
    DATABASE_ERROR = (5001, "数据库操作失败")
    SERVICE_UNAVAILABLE = (5002, "服务暂时不可用")
    TIMEOUT_ERROR = (5003, "请求超时")
    THIRD_PARTY_ERROR = (5004, "第三方服务错误")

    # 微信小程序错误
    WECHAT_LOGIN_FAILED = (1010, "微信登录失败")
    WECHAT_TOKEN_EXPIRED = (1011, "微信令牌已过期")
    WECHAT_USER_NOT_FOUND = (1012, "微信用户不存在")
    WECHAT_CODE_INVALID = (1013, "微信授权码无效,请重新获取授权")
    WECHAT_NETWORK_ERROR = (1014, "微信网络请求失败")
    WECHAT_INFO_FETCH_FAILED = (1015, "获取微信用户信息失败")
    WECHAT_OPENID_ERROR = (1016, "获取openId失败")
    WECHAT_LOGIN_SUCCESS = (1017, "获取openId成功")

    def __init__(self, code, message):
        self.code = code
        self.message = message

    def to_dict(self):
        """
        转换为字典格式
        """
        return {'code': self.code, 'message': self.message}

    @classmethod
    def custom_message(cls, status, custom_msg):
        """
        创建自定义消息的状态响应
        :param status: CustomStatus枚举值
        :param custom_msg: 自定义消息
        :return: 包含自定义消息的字典
        """
        return {'code': status.code, 'message': custom_msg}

    def to_response(self, data=None):
        """
        转换为完整的响应格式，包含数据
        :param data: 返回的数据内容
        :return: 完整的响应字典
        """
        response = {
            'code': self.code,
            'message': self.message
        }
        if data is not None:
            response['data'] = data
        return response
