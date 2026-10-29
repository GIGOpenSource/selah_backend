"""
ASGI config for Selah (uivsbe_backend).
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.pro')

application = get_asgi_application()
