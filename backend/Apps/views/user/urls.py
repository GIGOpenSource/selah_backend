#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
@Project ：NoBad 
@File    ：urls.py
@Author  ：LYP
@Date    ：2025/10/30 13:26 
@description :
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from Apps.views.user.view import UserViewSet

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')  # 添加 basename 参数



urlpatterns = [
    path('', include(router.urls)),
]
