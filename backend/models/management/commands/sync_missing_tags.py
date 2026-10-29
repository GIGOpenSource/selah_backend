#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""一次性脚本：扫描语录/壁纸 tag_index，把内容中用到但 Tag 表不存在的隐性标签补入库。

用法:
  python manage.py sync_missing_tags --settings=uivsbe_backend.settings.dev

仅用于数据修复，不要作为定时任务（大数据量下有性能开销）。
"""
from django.core.management.base import BaseCommand

from tools.tag_library import sync_missing_tags_from_content


class Command(BaseCommand):
    help = 'Sync missing implicit tags from content tag_index into Tag table'

    def handle(self, *args, **options):
        created = sync_missing_tags_from_content()
        total = sum(len(v) for v in created.values())
        if not total:
            self.stdout.write(self.style.SUCCESS('No missing tags found. All content tags are in the Tag table.'))
            return
        for type_key, names in sorted(created.items()):
            self.stdout.write(f'  {type_key}: {", ".join(names)}')
        self.stdout.write(self.style.SUCCESS(f'Created {total} missing tag(s) across {len(created)} type(s).'))
