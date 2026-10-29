#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah 埋点路由。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from Apps.views.analytics.view import AnalyticsViewSet

router = DefaultRouter()
router.register(r'', AnalyticsViewSet, basename='selah-analytics')
urlpatterns = [path('', include(router.urls))]
