#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Manna Quiz 路由（独立于 /api/content/quiz/）。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from Apps.views.quiz.view import MannaQuizViewSet

router = DefaultRouter()
router.register(r'', MannaQuizViewSet, basename='manna-quiz')

urlpatterns = [
    path('', include(router.urls)),
]
