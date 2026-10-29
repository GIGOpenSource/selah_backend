#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from django.db import IntegrityError
from rest_framework import viewsets, serializers
from rest_framework.decorators import action
from drf_spectacular.utils import extend_schema, extend_schema_view

from tools.permissions import IsTokenValid
from tools.token_tools import CustomTokenTool, generate_login_user_token
from tools.auth_helpers import persist_auth, clear_auth, get_token_from_request
from models.models import User
from tools.password_hasher import verify_password
from django.utils.translation import gettext as _
from tools.base_views import BaseViewSet
from tools.utils import ApiResponse, CustomPagination
from Apps.views.user.serializers import UserSerializer


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'password', 'confirm_password')

    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError(_("两次输入的密码不一致"))
        return data

    def create(self, validated_data):
        validated_data.pop('confirm_password')
        user = User.objects.create(**validated_data)
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


@extend_schema(tags=[_("用户管理")])
@extend_schema_view(
    list=extend_schema(summary=_('获取用户列表（需有效 Token）')),
)
class UserViewSet(viewsets.ViewSet):
    permission_classes_by_action = {
        'register': [],
        'login': [],
        'me': [],
        'list': [IsTokenValid],
        'retrieve': [IsTokenValid],
    }
    queryset = User.objects.all()
    serializer_class = RegisterSerializer

    def get_permissions(self):
        return [perm() for perm in self.permission_classes_by_action.get(self.action, [])]

    @extend_schema(
        request=RegisterSerializer,
        responses={
            201: {"type": "object", "properties": {"token": {"type": "string"}, "user_id": {"type": "integer"}}}},
        summary=_("用户注册"),
    )
    @action(detail=False, methods=['post'], url_path='register')
    def register(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return ApiResponse(message=str(serializer.errors), code=400)

        user = serializer.save()
        remember = bool(request.data.get('remember'))
        token = CustomTokenTool.generate_token(user_id=user.id, remember=remember)
        response = ApiResponse(
            data={"token": token, "user_id": user.id, "username": user.username},
            message=_('注册成功'),
            code=201
        )
        persist_auth(request, response, token, user.id, remember=remember)
        return response

    @extend_schema(
        request=LoginSerializer,
        responses={
            200: {"type": "object", "properties": {"token": {"type": "string"}, "user_id": {"type": "integer"}}}},
        summary=_("用户登录"),
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

        if verify_password(password, user.password):
            remember = bool(request.data.get('remember'))
            token = generate_login_user_token(request, user, remember=remember)
            response = ApiResponse(
                data={"token": token, "user_id": user.id, "username": user.username},
                message=_('登录成功')
            )
            persist_auth(request, response, token, user.id, remember=remember)
            return response
        return ApiResponse(message=_('用户名或密码错误'), code=400)

    @extend_schema(
        responses={200: {"type": "object", "properties": {
            "user_id": {"type": "integer"},
            "username": {"type": "string"},
            "role": {"type": "string"},
        }}},
        summary=_("获取当前登录用户信息")
    )
    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        token = get_token_from_request(request)
        if not token:
            return ApiResponse(message=_('未登录'), code=401)
        is_valid, user_id = CustomTokenTool.verify_token(token)
        if not is_valid:
            return ApiResponse(message=_('Token无效或已过期'), code=401)
        try:
            user = User.objects.get(id=user_id)
            return ApiResponse(data={
                "user_id": user.id,
                "username": user.username,
                "role": user.role,
            })
        except User.DoesNotExist:
            return ApiResponse(message=_('用户不存在'), code=404)

    def list(self, request):
        users = self.queryset.all().order_by('-created_at')
        paginator = CustomPagination()
        page = paginator.paginate_queryset(users, request)
        if page is not None:
            serializer = RegisterSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        serializer = RegisterSerializer(users, many=True)
        return ApiResponse(data=serializer.data)

    @extend_schema(
        responses={200: {"type": "object", "properties": {"message": {"type": "string"}}}},
        summary="用户登出"
    )
    @action(detail=False, methods=['post'], url_path='logout')
    def logout(self, request):
        token = get_token_from_request(request)
        if token:
            CustomTokenTool.delete_token(token)
        response = ApiResponse(message=_("登出成功，Token 已失效"))
        return clear_auth(request, response)


@extend_schema(tags=[_("用户管理-后台")])
@extend_schema_view(
    list=extend_schema(summary=_('获取用户列表')),
    retrieve=extend_schema(summary=_('获取用户详情')),
    create=extend_schema(summary=_('创建用户')),
    update=extend_schema(summary=_('更新用户')),
    partial_update=extend_schema(summary=_('部分更新用户')),
    destroy=extend_schema(summary=_('删除用户')),
)
class AdminUserViewSet(BaseViewSet):
    """后台用户管理 ViewSet"""
    queryset = User.objects.all().order_by('-created_at')
    serializer_class = UserSerializer
    pagination_class = CustomPagination
    filterset_fields = ['username', 'email', 'role', 'status']

    @extend_schema(
        responses={200: {"type": "object", "properties": {
            "user_id": {"type": "integer"},
            "status": {"type": "integer"},
        }}},
        summary=_("启用/禁用用户")
    )
    @action(detail=True, methods=['post'], url_path='toggle-status')
    def toggle_status(self, request, pk=None):
        user = self.get_object()
        user.status = 0 if user.status == 1 else 1
        user.save()
        return ApiResponse(
            data={"user_id": user.id, "status": user.status},
            message=_("状态已更新")
        )
