#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from drf_spectacular.utils import extend_schema, extend_schema_view
from django.utils.translation import gettext as _

from tools.base_views import BaseViewSet
from tools.utils import ApiResponse, CustomPagination
from models.models import Example
from Apps.views.Example.serializers import (
    ExampleSerializer,
    ExampleCreateSerializer,
    ExampleUpdateSerializer,
)


@extend_schema(tags=[_("示例管理")])
@extend_schema_view(
    list=extend_schema(summary=_('获取示例列表')),
    retrieve=extend_schema(summary=_('获取示例详情')),
    create=extend_schema(summary=_('创建示例')),
    update=extend_schema(summary=_('更新示例')),
    partial_update=extend_schema(summary=_('部分更新示例')),
    destroy=extend_schema(summary=_('删除示例')),
)
class ExampleViewSet(BaseViewSet):
    """示例视图集 — 演示 BaseViewSet + ApiResponse + CustomPagination 的标准用法"""
    queryset = Example.objects.filter(is_deleted=False)
    pagination_class = CustomPagination

    def get_serializer_class(self):
        if self.action == 'create':
            return ExampleCreateSerializer
        if self.action in ('update', 'partial_update'):
            return ExampleUpdateSerializer
        return ExampleSerializer

    def list(self, request, *args, **kwargs):
        """重写 list 以展示 CustomPagination + ApiResponse 的完整用法"""
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return ApiResponse(data=serializer.data, message="列表获取成功")

    def destroy(self, request, *args, **kwargs):
        """软删除：标记 is_deleted=True 而非物理删除"""
        instance = self.get_object()
        instance.is_deleted = True
        instance.save(update_fields=['is_deleted', 'updated_at'])
        return ApiResponse(message="删除成功")
