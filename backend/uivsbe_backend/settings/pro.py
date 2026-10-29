"""
Selah 生产环境配置
规范：UTC、logger、禁止 print、禁止 SQLite
"""
import logging
import os
from pathlib import Path

from django.utils.translation import gettext_noop
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR.parent / '.env'
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

LANGUAGES = [
    ('zh-hans', gettext_noop('Simplified Chinese')),
    ('zh-hant', gettext_noop('Traditional Chinese')),
    ('ja', gettext_noop('Japanese')),
    ('ko', gettext_noop('Korean')),
    ('en', gettext_noop('English')),
    ('de', gettext_noop('German')),
    ('fr', gettext_noop('French')),
    ('es', gettext_noop('Spanish')),
]
LOCALE_PATHS = [
    os.path.join(BASE_DIR.parent, 'locale'),
]

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY') or os.getenv('SECRET_KEY')
# 仅当进程以 settings.pro 启动时强制密钥；被 settings.dev import 时不拦截
_settings_mod = os.getenv('DJANGO_SETTINGS_MODULE', '')
if not SECRET_KEY and _settings_mod.endswith('settings.pro'):
    raise RuntimeError('缺少 DJANGO_SECRET_KEY：生产环境禁止使用默认密钥')

DEBUG = os.getenv('DJANGO_DEBUG', 'false').lower() in ('1', 'true', 'yes')

_allowed = os.getenv('ALLOWED_HOSTS', '*').strip()
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(',') if h.strip()]

INSTALLED_APPS = [
    'Apps',
    'models',
    'rest_framework',
    'drf_spectacular',
    'drf_spectacular_sidecar',
    'corsheaders',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'uivsbe_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'uivsbe_backend.wsgi.application'
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'tools.authentication.TokenAuthentication',
        'tools.authentication.CustomBasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '80/min',
        'user': '400/min',
        'translate': '20/min',
        'upload': '30/min',
        'chat': '40/min',
        'activity': '60/min',
        'device_login': '30/min',
    },
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'tools.utils.custom_exception_handler',
    'UNAUTHENTICATED_USER': None,
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Selah API',
    'DESCRIPTION': 'Selah 1.0 — daily affirmations / devotion API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
}

POSTGRES_HOST = (
    os.getenv('POSTGRES_HOST')
    or os.getenv('POSTGRES_BUILDMART_HOST')
    or os.getenv('DB_HOST')
)
if not POSTGRES_HOST:
    raise RuntimeError('缺少 POSTGRES_HOST：禁止使用 SQLite，请配置远程 PostgreSQL')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'uivsbe'),
        'USER': os.getenv('POSTGRES_USER'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD'),
        'HOST': POSTGRES_HOST,
        'PORT': os.getenv('POSTGRES_PORT', '55431'),
        'OPTIONS': {
            'connect_timeout': 15,
        },
    }
}
if 'sqlite' in DATABASES['default']['ENGINE'].lower():
    raise RuntimeError('禁止使用 SQLite')

STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR.parent, 'staticfiles')
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# 生产默认收紧 CORS；可用 CORS_ALLOW_ALL=true 放开联调
_cors_all = os.getenv('CORS_ALLOW_ALL', 'false').lower() in ('1', 'true', 'yes')
CORS_ALLOW_ALL_ORIGINS = _cors_all
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv('CORS_ALLOWED_ORIGINS', '').split(',')
    if o.strip()
]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = list({
    'accept', 'accept-encoding', 'authorization', 'content-type',
    'dnt', 'origin', 'user-agent', 'x-csrftoken', 'x-requested-with',
    'token', 'is-dev', 'accept-language',
    # App H5 / 白标作用域头（mobile http.js）
    'x-app-key', 'x-platform',
})

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.getenv('LOG_LEVEL', 'INFO'),
    },
}

logger.info(
    'Selah PRO settings loaded host=%s db=%s debug=%s',
    POSTGRES_HOST,
    DATABASES['default']['NAME'],
    DEBUG,
)
