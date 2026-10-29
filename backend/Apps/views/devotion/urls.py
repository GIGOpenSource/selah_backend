#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from Apps.views.devotion.view import DevotionViewSet

router = DefaultRouter()
router.register(r'', DevotionViewSet, basename='devotion')

urlpatterns = [
    path('', include(router.urls)),
]
