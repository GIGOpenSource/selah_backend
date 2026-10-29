#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from django.db import IntegrityError
from rest_framework import viewsets
from rest_framework.decorators import action
from drf_spectacular.utils import extend_schema, extend_schema_view

from tools.permissions import IsTokenValid, IsAdmin
from tools.token_tools import CustomTokenTool, generate_login_user_token
from tools.auth_helpers import persist_auth, clear_auth, get_token_from_request
from models.models import User
from tools.password_hasher import verify_password
from tools.utils import ApiResponse, CustomPagination
from django.utils.translation import gettext as _
from Apps.views.admin.serializers import (
    AdminRegisterSerializer,
    AdminLoginSerializer,
    AdminUserSerializer,
)


@extend_schema(tags=[_("管理员管理")])
@extend_schema_view(
    list=extend_schema(summary=_('获取管理员列表')),
)
class AdminViewSet(viewsets.ViewSet):
    """管理员登录注册视图"""
    permission_classes_by_action = {
        'register': [],
        'login': [],
        'list': [IsTokenValid, IsAdmin],
        'info': [IsTokenValid, IsAdmin],
    }

    def get_permissions(self):
        return [perm() for perm in self.permission_classes_by_action.get(self.action, [])]

    @extend_schema(
        request=AdminRegisterSerializer,
        responses={
            201: {"type": "object", "properties": {
                "token": {"type": "string"},
                "user_id": {"type": "integer"},
                "username": {"type": "string"}
            }}
        },
        summary=_("管理员注册"),
    )
    @action(detail=False, methods=['post'], url_path='register')
    def register(self, request):
        serializer = AdminRegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return ApiResponse(message=str(serializer.errors), code=400)

        user = serializer.save()
        remember = bool(request.data.get('remember'))
        token = CustomTokenTool.generate_token(user_id=user.id, remember=remember)
        response = ApiResponse(
            data={
                "token": token,
                "user_id": user.id,
                "username": user.username,
                "role": user.role
            },
            message=_('注册成功'),
            code=201
        )
        persist_auth(request, response, token, user.id, remember=remember)
        return response

    @extend_schema(
        request=AdminLoginSerializer,
        responses={
            200: {"type": "object", "properties": {
                "token": {"type": "string"},
                "user_id": {"type": "integer"},
                "username": {"type": "string"},
                "role": {"type": "string"}
            }}
        },
        summary=_("管理员登录")
    )
    @action(detail=False, methods=['post'], url_path='login')
    def login(self, request):
        username = request.data.get('username')
        password = request.data.get('password')

        if not username or not password:
            return ApiResponse(message=_('用户名和密码不能为空'), code=400)

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return ApiResponse(message=_('用户名不存在'), code=400)
        except IntegrityError:
            return ApiResponse(message=_('用户名重复'), code=400)

        if user.role not in ['admin', 'operator', 'super_admin']:
            return ApiResponse(message=_('该账号不是管理员账号'), code=403)

        if user.status == 0:
            return ApiResponse(message=_('该账号已被禁用'), code=403)

        if verify_password(password, user.password):
            remember = bool(request.data.get('remember'))
            token = generate_login_user_token(request, user, remember=remember)
            response = ApiResponse(
                data={
                    "token": token,
                    "user_id": user.id,
                    "username": user.username,
                    "role": user.role
                },
                message=_('登录成功')
            )
            persist_auth(request, response, token, user.id, remember=remember)
            return response
        return ApiResponse(message=_('用户名或密码错误'), code=400)

    @extend_schema(
        responses={200: AdminUserSerializer},
        summary=_("获取当前管理员信息")
    )
    @action(detail=False, methods=['get'], url_path='info')
    def info(self, request):
        token = get_token_from_request(request)
        is_valid, user_id = CustomTokenTool.verify_token(token)

        if not is_valid:
            return ApiResponse(message=_('Token无效或已过期'), code=401)

        try:
            user = User.objects.get(id=user_id)
            serializer = AdminUserSerializer(user)
            return ApiResponse(data=serializer.data)
        except User.DoesNotExist:
            return ApiResponse(message=_('用户不存在'), code=404)

    @extend_schema(
        responses={200: {"type": "object", "properties": {"message": {"type": "string"}}}},
        summary=_("管理员登出")
    )
    @action(detail=False, methods=['post'], url_path='logout')
    def logout(self, request):
        token = get_token_from_request(request)
        if token:
            CustomTokenTool.delete_token(token)
        response = ApiResponse(message=_("登出成功，Token 已失效"))
        return clear_auth(request, response)

    def list(self, request):
        admins = User.objects.filter(role__in=['admin', 'operator', 'super_admin']).order_by('-created_at')
        paginator = CustomPagination()
        page = paginator.paginate_queryset(admins, request)
        if page is not None:
            serializer = AdminUserSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        serializer = AdminUserSerializer(admins, many=True)
        return ApiResponse(data=serializer.data)
