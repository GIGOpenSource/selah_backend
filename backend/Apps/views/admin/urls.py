#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from Apps.views.admin.view import AdminViewSet

router = DefaultRouter()
router.register('', AdminViewSet, basename='admin')

urlpatterns = [
    path('', include(router.urls)),
]
