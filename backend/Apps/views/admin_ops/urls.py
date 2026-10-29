#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah 运营后台路由。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from Apps.views.admin_ops.view import AdminOpsViewSet

router = DefaultRouter()
router.register(r'', AdminOpsViewSet, basename='selah-admin-ops')
urlpatterns = [path('', include(router.urls))]
