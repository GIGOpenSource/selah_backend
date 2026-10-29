#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah 内容与冷启引导路由。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from Apps.views.content.view import ContentViewSet, OnboardingViewSet
from Apps.views.content.quiz_view import QuizViewSet

router = DefaultRouter()
router.register(r'', ContentViewSet, basename='selah-content')
router.register(r'onboarding', OnboardingViewSet, basename='selah-onboarding')
router.register(r'quiz', QuizViewSet, basename='selah-quiz')

urlpatterns = [path('', include(router.urls))]
