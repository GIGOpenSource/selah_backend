"""Selah API 根路由。

挂载：健康检查、法务占位页、OpenAPI 文档、资源上传，以及 Apps.urls 下全部业务前缀。
"""
import os
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from django.http import JsonResponse
from django.db import connection

from tools.uploader_data import UploadResourceView
from uivsbe_backend.bible_media import bible_media
from uivsbe_backend.legal_views import legal_page


def home(request):
    """根路径欢迎信息（非业务接口）。"""
    return JsonResponse({
        'message': 'Welcome to Selah API',
        'version': '1.0.0',
        'brand': 'Selah',
    })


def health(request):
    """K8s / compose 就绪探针：DB + Redis"""
    status = {'ok': True, 'db': False, 'redis': False}
    code = 200
    try:
        connection.ensure_connection()
        status['db'] = True
    except Exception as e:
        status['ok'] = False
        status['db_error'] = str(e)[:200]
        code = 503
    try:
        from tools.token_tools import _redis
        pong = _redis.client.ping()
        status['redis'] = bool(pong)
        if not pong:
            status['ok'] = False
            code = 503
    except Exception as e:
        status['ok'] = False
        status['redis_error'] = str(e)[:200]
        code = 503
    return JsonResponse(status, status=code)


urlpatterns = [
    path('', home, name='home'),
    path('health', health, name='health'),
    path('api/health/', health, name='api-health'),
    path('legal/<slug:slug>/', legal_page, name='legal'),
    path('bible-media/<path:relpath>', bible_media, name='bible-media'),
    path('api/', include('Apps.urls')),
    path('api/upload/', UploadResourceView.as_view(), name='upload_resource'),
]

if os.getenv('DJANGO_DEBUG', 'false').lower() in ('1', 'true', 'yes'):
    urlpatterns += [
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
        path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    ]
