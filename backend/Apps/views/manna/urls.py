#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Manna App 路由。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from Apps.views.manna.view import MannaViewSet

router = DefaultRouter()
router.register(r'', MannaViewSet, basename='manna')

urlpatterns = [
    path('', include(router.urls)),
]
