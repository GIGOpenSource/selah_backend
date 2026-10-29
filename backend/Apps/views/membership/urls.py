#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah 会员/IAP 路由。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from Apps.views.membership.view import MembershipViewSet

router = DefaultRouter()
router.register(r'', MembershipViewSet, basename='selah-membership')
urlpatterns = [path('', include(router.urls))]
