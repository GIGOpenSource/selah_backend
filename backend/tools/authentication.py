# tools/authentication.py
"""
DRF 认证类（现行）：Swagger Basic 登录 + App/运营 Token 解析。

- CustomBasicAuthentication：用户名/邮箱 + bcrypt 密码，签发 token（Swagger）
- TokenAuthentication：从 header/Authorization/Cookie 取 token，经 auth_session 解析 User
  无效 token 返回 None，由 permission（如 IsTokenValid）决定是否拒绝

Token 提取顺序见 auth_helpers.get_token_from_request：
  1) 请求头 token
  2) Authorization: Token xxx / Bearer xxx
  3) Cookie auth_token

说明：authentication1.py 为历史草稿，请勿使用；以本模块为准。
"""
from rest_framework.authentication import BasicAuthentication
from rest_framework.exceptions import AuthenticationFailed

from django.utils.translation import gettext_lazy as _

from models.models import User
from tools.password_hasher import verify_password
from tools.token_tools import generate_is_user_token
from tools.auth_helpers import get_token_from_request
from tools.auth_session import resolve_user_by_token


class CustomBasicAuthentication(BasicAuthentication):
    """Swagger / Basic：自定义 bcrypt 校验，成功后签发登录 token。"""
    def authenticate_credentials(self, userid, password, request=None):
        # 先按 username，再按 email；都不存在则统一「无效」避免枚举
        try:
            user = User.objects.get(username=userid)
        except User.DoesNotExist:
            # email 按 app_key 可重复；Basic/Swagger 优先运营角色，其次唯一命中
            qs = User.objects.filter(email=userid)
            user = (
                qs.filter(role__in=('operator', 'admin', 'super_admin')).first()
                or qs.filter(login_type='password').first()
            )
            if user is None:
                if qs.count() == 1:
                    user = qs.first()
                else:
                    raise AuthenticationFailed(_('无效的用户名或密码'))
        if not verify_password(password, user.password):
            raise AuthenticationFailed(_('无效的用户名或密码'))
        # 签发 Login: 前缀 AES token，写入 Redis（见 token_tools）
        token = generate_is_user_token(request, user)
        return (user, token)

    def authenticate_header(self, request):
        return 'Basic realm="%s"' % self.www_authenticate_realm


from drf_spectacular.extensions import OpenApiAuthenticationExtension


class CustomBasicAuthSchema(OpenApiAuthenticationExtension):
    """OpenAPI：CustomBasicAuthentication 的 security scheme。"""
    target_class = 'tools.authentication.CustomBasicAuthentication'
    name = 'CustomBasicAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'basic',
            'description': "使用用户名/密码登录（密码验证逻辑：截断72字节后验证）"
        }


class TokenAuthentication(BasicAuthentication):
    """
    基于 Token 的认证：支持 token 请求头、Authorization、Cookie。

    设计：无效/缺失 token 返回 None（不抛），让 IsTokenValid 等 permission
    统一返回业务拒绝；避免认证层与权限层双重 401 语义混乱。
    """
    def authenticate(self, request):
        token = get_token_from_request(request)
        if not token:
            return None

        # 进程短缓存：命中则跳过 Redis + User 双跳（auth_session）
        user = resolve_user_by_token(token)
        if not user:
            return None  # 无效token视为未认证，不抛异常

        # DRF 约定：(user, auth)；auth 此处为原始 token 字符串
        return (user, token)

    def authenticate_header(self, request):
        return 'Token realm="API"'


class TokenAuthSchema(OpenApiAuthenticationExtension):
    """OpenAPI：TokenAuthentication 的 apiKey(header=token) 定义。"""
    target_class = 'tools.authentication.TokenAuthentication'
    name = 'TokenAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'token',
            'description': '通过token进行认证，支持 header: token / Authorization: Token xxx / Cookie'
        }
