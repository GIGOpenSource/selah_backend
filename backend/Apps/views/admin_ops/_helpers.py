#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Admin-ops mixin 共用符号（不依赖 view.py，避免循环导入）。"""
from django.db.models import Q
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from models.models import (
    Quote, TagType, Tag, DisplayTag, DisplayStructureState, RecommendStrategy,
)
from Apps.views.admin_ops.serializers import IdSerializer
from tools.admin_rbac import PERM_CONTENT, has_perm
from tools.admin_scope import deny_staff_app_write
from tools.selah_quota import normalize_lang, normalize_platform, normalize_app_key
from tools.utils import ApiResponse, CustomPagination

# import * 默认不带出以下划线开头的名字，必须显式列入
__all__ = [
    'Q', '_', 'extend_schema', 'action',
    'Quote', 'TagType', 'Tag', 'DisplayTag', 'DisplayStructureState', 'RecommendStrategy',
    'IdSerializer', 'PERM_CONTENT', 'has_perm',
    'normalize_lang', 'normalize_platform', 'normalize_app_key',
    'ApiResponse', 'CustomPagination', '_deny_app_write',
]


def _deny_app_write(request, app_key):
    """Staff 不可写目标 App 时返回 403 响应。"""
    msg = deny_staff_app_write(request.user, app_key)
    if msg:
        return ApiResponse(message=msg, code=403)
    return None
