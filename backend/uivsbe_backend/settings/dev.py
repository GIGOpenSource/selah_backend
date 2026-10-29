"""
Selah 开发环境 — 强制 PostgreSQL + Redis（禁止 SQLite）
配置来源：backend/.env（参考 .env.example）
"""
from .pro import *  # noqa: F401,F403
import logging
import os

logger = logging.getLogger(__name__)

DEBUG = True
TIME_ZONE = 'UTC'
USE_TZ = True

# 开发可回退到固定密钥，避免本地缺 env 起不来
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY') or os.getenv('SECRET_KEY') or (
    'django-insecure-selah-dev-only-change-me'
)

ALLOWED_HOSTS = ['*']
CORS_ALLOW_ALL_ORIGINS = True

_pg_host = (
    os.getenv('POSTGRES_HOST')
    or os.getenv('DB_HOST')
)
if not _pg_host:
    raise RuntimeError(
        '缺少 POSTGRES_HOST：请复制 .env.example 为 .env 并配置 PostgreSQL'
    )

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'selah'),
        'USER': os.getenv('POSTGRES_USER'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD'),
        'HOST': _pg_host,
        'PORT': os.getenv('POSTGRES_PORT', '5432'),
        'OPTIONS': {
            'connect_timeout': 15,
        },
    }
}

if 'sqlite' in DATABASES['default']['ENGINE'].lower():
    raise RuntimeError('禁止使用 SQLite，请使用 (1).env 中的 PostgreSQL')

SPECTACULAR_SETTINGS = {
    **SPECTACULAR_SETTINGS,
    'TITLE': 'Selah API',
    'DESCRIPTION': 'Selah 1.0 — daily affirmations / devotion API',
    'VERSION': '1.0.0',
}

logger.info(
    'Selah DEV settings: PostgreSQL %s:%s/%s',
    _pg_host,
    DATABASES['default']['PORT'],
    DATABASES['default']['NAME'],
)
