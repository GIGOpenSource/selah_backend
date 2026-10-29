#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from Apps.views.bible.view import BibleViewSet

router = DefaultRouter()
router.register(r'', BibleViewSet, basename='bible')

urlpatterns = [
    path('', include(router.urls)),
]
