"""
DRF 权限类：登录态校验、后台角色、对象归属等。

常用：
- IsTokenValid：有效 token → 写入 request.user；App 用户同步配置 ContextVar
- IsOwner / IsAdmin：后台 role（operator/admin/super_admin）
- IsCustomerTokenValid：C 端 CToken（历史路径）
- IsOwnerOrAdmin：对象级归属或管理员
其余 URLAuthorization / HeaderAuthorization / NotAuthenticated 为占位或兜底。

ContextVar（selah_quota._cfg_language / _cfg_platform）：
  IsTokenValid 在 role=user 时 set_config_context_from_user，
  管理员/失败则 clear，保证 get_config 八级回退用对语言×平台。
"""
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from models.models import User
from tools.token_tools import CustomTokenTool
from tools.auth_helpers import get_token_from_request
from tools.auth_session import resolve_user_by_token, is_resolved_user


class IsStaffUser(BasePermission):
    """允许 is_staff=True 或 is_active=True 的用户访问（偏 Django 原生字段，少用）。"""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        return request.user.is_staff or request.user.is_active


class IsOwner(BasePermission):
    """允许后台角色 operator / admin / super_admin 访问（不区分细权限码，见 admin_rbac）。"""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        # 只允许 User 表的管理员
        if isinstance(request.user, User):
            if request.user.role in ['admin', 'operator', 'super_admin']:
                return True
        return False


class IsAdmin(BasePermission):
    """
    后台管理员入口权限：role 须为 operator / admin / super_admin。
    无 role 或不在列表则 AuthenticationFailed（401 风格业务码）。
    """
    def has_permission(self, request, view):
        try:
            role = request.user.role
        except AttributeError:
            raise AuthenticationFailed({"code": 401, "message": "管理员无role权限"})
        if not request.user or not role:
            raise AuthenticationFailed({"code": 401, "message": "请提供有效的管理员token"})
        try:
            if role in ['operator', 'admin','super_admin']:
                return True
            else:
                raise AuthenticationFailed({"code": 401, "message": "管理员role权限不够"})
        except User.DoesNotExist:
            raise AuthenticationFailed('token对应的管理员不存在')

class IsTokenValid(BasePermission):
    """
    仅允许携带有效 Token 的请求；解析 User 写入 request.user。

    App 用户（role=user）：写入 app_key×language×platform ContextVar，后续 get_config 走用户维度回退。
    管理员：clear_config_context，读配置应显式传 language/platform，避免串用 App 用户上下文。
    """
    message = "Token 无效或已过期"  # 权限拒绝时的提示

    def has_permission(self, request, view):
        from tools.selah_quota import set_config_context_from_user, clear_config_context

        # TokenAuthentication 已解析则复用，避免同请求内再 Redis+DB
        user = request.user if is_resolved_user(getattr(request, 'user', None)) else None
        if user is None:
            user = resolve_user_by_token(get_token_from_request(request))
        if not user:
            clear_config_context()
            return False

        request.user = user
        # App 用户：按 app_key × system_lang × platform 解析运营配置；管理员读配置走显式参数
        if getattr(user, 'role', None) == 'user':
            set_config_context_from_user(user)
        else:
            clear_config_context()
        return True


class IsCustomerTokenValid(BasePermission):
    """
    C 端客户 Token（请求头 token，值为 CToken 开头）。

    历史路径：校验成功后写 request.customer_id；与 Login: App token 体系分离。
    """
    message = "客户 Token 无效或已过期"

    def has_permission(self, request, view):
        # 仅认 header.token，不走 Cookie / Bearer 兼容链
        token = request.headers.get("token")
        is_valid, customer_id = CustomTokenTool.verify_customer_token(token)
        if is_valid:
            request.customer_id = int(customer_id)
            return True
        raise AuthenticationFailed({"code": 401, "message": "请先登录"})


class IsOwnerOrAdmin(BasePermission):
    """对象级：管理员可操作全部；否则仅 customer_upload 归属本人。"""
    message = "您没有权限访问此对象"  # 权限拒绝时的提示

    def has_object_permission(self, request, view, obj):
        from models.models import User

        # 管理员可以操作所有对象
        if isinstance(request.user, User):
            if hasattr(request.user, 'role') and request.user.role in ['admin', 'operator', 'super_admin']:
                return True

        # 客户用户只能操作自己上传的壁纸
        if isinstance(request.user):
            # 检查是否有 customer_upload 关系
            if hasattr(obj, 'customer_upload'):
                upload_relation = obj.customer_upload
                if upload_relation and upload_relation.customer_id == request.user.id:
                    return True
        return False


class URLAuthorization(BasePermission):
    """占位：曾按 query token 校验 URL，当前未形成完整鉴权逻辑。"""
    def authenticate(self, request):
        # 获取当前请求的 URL
        url = request.path
        # 获取当前用户
        user = request.user
        token = request.query_params.get('token')
        if not token:
            return
        return True

class HeaderAuthorization(BasePermission):
    """占位：曾按 Authorization 头校验，当前未形成完整鉴权逻辑。"""
    def authenticate(self, request):
        # 获取当前请求的 Header
        token = request.META.get('HTTP_AUTHORIZATION')
        # 获取当前用户
        if not token:
            return
        return True

class NotAuthenticated(BasePermission):
    """兜底：未认证时抛出业务码 20000。"""
    def authenticate(self, request):
        raise AuthenticationFailed({"code":20000,"message":'用户未认证'})
    def authenticate_header(self, request):
        return 'NotAuthenticated'
