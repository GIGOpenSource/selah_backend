"""
WSGI config for Selah (uivsbe_backend).
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.pro')

application = get_wsgi_application()
