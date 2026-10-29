#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah AI 路由。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from Apps.views.ai.view import AiViewSet

router = DefaultRouter()
router.register(r'', AiViewSet, basename='selah-ai')
urlpatterns = [path('', include(router.urls))]
