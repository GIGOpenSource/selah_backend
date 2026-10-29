#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
@Project ：UIVSBE
@File    ：urls.py
@description : 应用路由配置 — Selah 1.0（仅业务面）
"""
from django.urls import path, include

urlpatterns = [
    # 模板遗留 users/admin/example 已卸载；代码保留在 Apps/views/ 供参考
    path('auth/', include('Apps.views.auth.urls')),
    path('content/', include('Apps.views.content.urls')),
    path('checkin/', include('Apps.views.checkin.urls')),
    path('ai/', include('Apps.views.ai.urls')),
    path('membership/', include('Apps.views.membership.urls')),
    path('admin-ops/', include('Apps.views.admin_ops.urls')),
    path('analytics/', include('Apps.views.analytics.urls')),
    path('manna/', include('Apps.views.manna.urls')),
    # App 端前缀为 /api/Manna/（大小写敏感）；与小写并存
    path('Manna/', include('Apps.views.manna.urls')),
    path('bible/', include('Apps.views.bible.urls')),
    path('devotion/', include('Apps.views.devotion.urls')),
    path('quiz/', include('Apps.views.quiz.urls')),
]
