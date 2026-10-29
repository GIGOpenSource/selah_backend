"""历史中间件式权限示例。

正式鉴权请使用 tools.permissions.IsTokenValid / IsAdmin（含语言×平台 ContextVar）。
本文件仅作早期实现参考，业务 View 不应再依赖此处。
"""
from rest_framework.permissions import BasePermission

from models.models import User
from tools.token_tools import CustomTokenTool
from tools.auth_helpers import get_token_from_request


class IsTokenValid(BasePermission):
    """仅校验 Token 是否有效并注入 request.user（无配置上下文副作用）。"""
    message = "Token 无效或已过期"

    def has_permission(self, request, view):
        token = get_token_from_request(request)
        if not token:
            return False

        is_valid, user_id = CustomTokenTool.verify_token(token)
        if not is_valid or not user_id:
            return False

        try:
            request.user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return False
        return True
