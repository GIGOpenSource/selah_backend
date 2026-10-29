#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah 认证路由。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from Apps.views.auth.view import AuthViewSet

router = DefaultRouter()
router.register(r'', AuthViewSet, basename='selah-auth')

urlpatterns = [
    path('', include(router.urls)),
]
