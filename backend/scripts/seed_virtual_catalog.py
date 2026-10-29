#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""补齐虚拟运营目录（语录/话题/壁纸/Welcome/Onboarding/SKU 价/社交链）。

用法：
  ./uivsbe_env/bin/python scripts/seed_virtual_catalog.py
"""
import os
import sys

import django

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from tools.selah_virtual_seed import ensure_virtual_catalog  # noqa: E402


def main():
    summary = ensure_virtual_catalog()
    print('done', summary)


if __name__ == '__main__':
    main()
