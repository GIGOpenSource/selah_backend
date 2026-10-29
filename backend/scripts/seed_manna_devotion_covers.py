#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""将 Manna 灵修系列封面上传 COS，并写回 DevotionSeries.cover_url。

素材目录：content/manna/assets/devotion/
不写入 mobile-manna/static（封面只走存储 + 数据库）。

用法：
  ./uivsbe_env/bin/python scripts/seed_manna_devotion_covers.py
  ./uivsbe_env/bin/python scripts/seed_manna_devotion_covers.py --dry-run
  ./uivsbe_env/bin/python scripts/seed_manna_devotion_covers.py --force
"""
import argparse
import os
import sys
import uuid
from pathlib import Path

import django

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from models.models import DevotionSeries  # noqa: E402
from tools.selah_quota import MANNA_APP_KEY  # noqa: E402
from tools.uploader_data import upload_image_to_cos  # noqa: E402

ASSET_DIR = Path(__file__).resolve().parents[2] / 'content' / 'manna' / 'assets' / 'devotion'

# 系列标题 → 本地素材文件名（上传后 URL 写入 cover_url）
COVER_FILES = {
    'Overcoming Anxiety': 'featured-anxiety.png',
    'Learning to Love': 'thumb-love.png',
    'Building the Family': 'thumb-family.png',
    'Rekindling Hope': 'thumb-hope.png',
    'From Fear to Faith': 'thumb-fear.png',
}


def upload_file(path: Path, dry: bool) -> str:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    body = path.read_bytes()
    key = f'manna/devotion/covers/{uuid.uuid4().hex[:12]}_{path.name}'
    if dry:
        print(f'  [dry] would upload {path.name} → {key} ({len(body) // 1024}KB)')
        return f'https://example.invalid/{key}'
    result = upload_image_to_cos(body, key)
    if not result or not result.get('url'):
        raise RuntimeError(f'COS upload failed: {path.name}')
    return result['url']


def seed_covers(*, dry: bool = False, force: bool = False) -> tuple[int, int]:
    """Upload covers to COS and write DevotionSeries.cover_url. Returns (updated, skipped)."""
    if not ASSET_DIR.is_dir():
        raise FileNotFoundError(f'asset dir missing: {ASSET_DIR}')

    updated = 0
    skipped = 0
    for title, filename in COVER_FILES.items():
        path = ASSET_DIR / filename
        qs = DevotionSeries.objects.filter(app_key=MANNA_APP_KEY, title=title)
        rows = list(qs)
        if not rows:
            print(f'skip (no DB row): {title}')
            skipped += 1
            continue
        for row in rows:
            if row.cover_url and not force:
                print(f'skip (has cover): {title} id={row.id}')
                skipped += 1
                continue
            url = upload_file(path, dry)
            if not dry:
                row.cover_url = url
                row.save(update_fields=['cover_url'])
            print(f'ok {title} id={row.id} → {url}')
            updated += 1
    return updated, skipped


def main():
    parser = argparse.ArgumentParser(description='Upload Manna devotion covers to COS and set DB cover_url')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--force', action='store_true', help='Overwrite existing non-empty cover_url')
    args = parser.parse_args()

    try:
        updated, skipped = seed_covers(dry=args.dry_run, force=args.force)
    except Exception as e:
        print(f'FAIL: {e}', file=sys.stderr)
        return 1

    print(f'done updated={updated} skipped={skipped} dry={args.dry_run}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
