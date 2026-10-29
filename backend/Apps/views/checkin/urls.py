#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah 打卡路由。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from Apps.views.checkin.view import CheckinViewSet

router = DefaultRouter()
router.register(r'', CheckinViewSet, basename='selah-checkin')
urlpatterns = [path('', include(router.urls))]
