#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""到期软删冷静期账号。

用法:
  python manage.py purge_pending_deletions --settings=uivsbe_backend.settings.dev

建议 cron 每小时或每天跑 1～2 次。
"""
from django.core.management.base import BaseCommand

from tools.account_deletion import purge_due_deletions


class Command(BaseCommand):
    help = 'Soft-delete users whose deletion cooling period has expired'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=500,
            help='Max users to process per run (default 500)',
        )

    def handle(self, *args, **options):
        limit = max(1, int(options['limit'] or 500))
        n = purge_due_deletions(limit=limit)
        self.stdout.write(self.style.SUCCESS(f'purged {n} user(s)'))
