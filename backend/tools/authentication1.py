# tools/authentication1.py
"""
【遗留/未使用】历史认证草稿副本，逻辑不完整且与现行实现冲突。
请使用 tools.authentication（TokenAuthentication / CustomBasicAuthentication）。
本文件勿接入 REST_FRAMEWORK AUTHENTICATION_CLASSES。
"""
from django.contrib.auth import get_user_model
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.plumbing import build_bearer_security_scheme_object
from rest_framework import authentication, exceptions
from rest_framework import serializers
from rest_framework.authentication import BasicAuthentication
from rest_framework.exceptions import AuthenticationFailed

from models.models import User
from tools.token_tools import CustomTokenTool


# 补全缺失的认证类（核心）
class BcryptTokenAuthentication(authentication.BaseAuthentication):
    keyword = 'Token'  # 与Swagger配置中的前缀一致

    def authenticate(self, request):
        auth_header = authentication.get_authorization_header(request).split()

        if not auth_header or auth_header[0].lower() != self.keyword.lower().encode():
            return None  # 未提供Token，交给其他认证类处理

        if len(auth_header) == 1:
            raise exceptions.AuthenticationFailed('Token格式错误：未提供令牌值')
        elif len(auth_header) > 2:
            raise exceptions.AuthenticationFailed('Token格式错误：令牌值不能包含空格')

        try:
            token = auth_header[1].decode()
            user_id = CustomTokenTool.verify_token(token)  # 验证token并获取用户ID
            user = User.objects.get(id=user_id)
            return (user, token)
        except (User.DoesNotExist, ValueError):
            raise exceptions.AuthenticationFailed('无效的Token')


# Swagger文档支持（保持不变）
class BcryptTokenAuthenticationSchema(OpenApiAuthenticationExtension):
    target_class = 'tools.authentication.BcryptTokenAuthentication'
    name = 'BcryptTokenAuth'

    def get_security_definition(self, auto_schema):
        return build_bearer_security_scheme_object(
            header_name='Authorization',
            token_prefix=self.target.keyword,
            bearer_format='Token <token>'
        )
from tools.password_hasher import verify_password

# tools/authentication.py
from drf_spectacular.extensions import OpenApiAuthenticationExtension

from rest_framework.authentication import BasicAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from django.contrib.auth import get_user_model
from tools.password_hasher import verify_password




class CustomBasicAuthentication(BasicAuthentication):
    # 你的认证逻辑（保持不变）
    def authenticate_credentials(self, userid, password, request=None):
        try:
            user = User.objects.get(username=userid)
        except User.DoesNotExist:
            raise AuthenticationFailed('无效的认证信息')

        if not user.is_active:
            raise AuthenticationFailed('用户已被禁用')

        if not verify_password(password, user.password):
            raise AuthenticationFailed('无效的认证信息')

        return (user, None)


# 关键：注册 Swagger 文档支持
class CustomBasicAuthSchema(OpenApiAuthenticationExtension):
    target_class = 'tools.authentication.CustomBasicAuthentication'  # 自定义类路径
    name = 'CustomBasicAuth'  # 文档中显示的认证方案名称

    def get_security_definition(self, auto_schema):
        # 定义 Basic 认证的文档显示格式
        return build_basic_security_scheme_object(
            description="使用用户名和密码进行认证（基于自定义密码验证逻辑）"
        )