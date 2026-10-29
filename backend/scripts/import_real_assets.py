#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
将 Figma / 互联网实拍图裁切后上传腾讯云 COS，并回写 Wallpaper.image_url / Topic.cover_url。

优先使用 Doc/design/assets_import/figma_*；不足时用 web/。

用法：
  ./uivsbe_env/bin/python scripts/import_real_assets.py
  ./uivsbe_env/bin/python scripts/import_real_assets.py --dry-run
"""
import argparse
import io
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import django
from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from django.conf import settings

from models.models import Topic, Wallpaper
from tools.uploader_data import upload_image_to_cos

ASSET_ROOT = Path(__file__).resolve().parents[3] / 'Doc' / 'design' / 'assets_import'
FIGMA_WP = ASSET_ROOT / 'figma_wallpaper'
FIGMA_TH = ASSET_ROOT / 'figma_themes'
WEB = ASSET_ROOT / 'web'
OUT_WP = ASSET_ROOT / 'out' / 'wallpapers'
OUT_TP = ASSET_ROOT / 'out' / 'topics'

# (key, is_vip, tags, sort_weight, preferred local paths relative to ASSET_ROOT)
WALLPAPER_SPECS = [
    ('dawn', False, ['calm', 'sunrise', 'photo'], 100, ['web/dawn.jpg', 'figma_wallpaper/w05.jpeg']),
    ('mist', False, ['calm', 'photo'], 95, ['web/mist.jpg', 'figma_themes/t02.jpeg']),
    ('sand', False, ['warm', 'beach', 'photo'], 90, ['figma_wallpaper/w01.jpeg', 'web/sand.jpg']),
    ('ocean', False, ['ocean', 'photo'], 85, ['web/ocean.jpg', 'figma_wallpaper/w05.jpeg']),
    ('forest', False, ['forest', 'photo'], 80, ['web/forest.jpg', 'figma_themes/t01.jpeg']),
    ('meadow', False, ['meadow', 'photo'], 75, ['web/meadow.jpg', 'web/lavender.jpg']),
    ('sky', False, ['sky', 'photo'], 70, ['web/sky.jpg', 'web/clouds.jpg']),
    ('soft', False, ['soft', 'photo'], 65, ['web/soft.jpg', 'figma_themes/t03.jpeg']),
    ('minimal', False, ['minimal', 'photo'], 60, ['web/minimal.jpg', 'web/lake.jpg']),
    ('dusk', False, ['calm', 'nature', 'photo'], 55, ['web/dusk.jpg', 'figma_wallpaper/w04.jpeg']),
    ('night', True, ['premium', 'night', 'photo'], 50, ['web/night.jpg', 'figma_themes/t14.jpeg']),
    ('gold', True, ['premium', 'gold', 'photo'], 45, ['web/gold.jpg', 'figma_themes/t10.jpeg']),
    ('deep', True, ['premium', 'abstract', 'photo'], 40, ['web/deep.jpg', 'figma_themes/t19.jpeg']),
]

# topic name substr -> cover candidates
TOPIC_COVERS = {
    'Peace': ['figma_themes/t08.jpeg', 'figma_themes/t03.jpeg', 'web/peace.jpg'],
    'Prayer': ['figma_themes/t17.jpeg', 'figma_themes/t05.jpeg', 'web/prayer.jpg'],
    'Hope': ['figma_themes/t04.jpeg', 'figma_themes/t15.jpeg', 'web/hope.jpg'],
    'Courage': ['figma_themes/t12.jpeg', 'web/courage.jpg', 'web/meadow.jpg'],
    'Healing': ['figma_themes/t01.jpeg', 'figma_themes/t18.jpeg', 'web/healing.jpg'],
    'Family': ['figma_themes/t09.jpeg', 'web/family.jpg', 'web/sand.jpg'],
    'Work': ['figma_themes/t06.jpeg', 'figma_themes/t11.jpeg', 'web/work.jpg'],
    'Advent': ['figma_themes/t14.jpeg', 'web/advent.jpg', 'web/night.jpg'],
}


def _assert_remote():
    db = settings.DATABASES['default']
    if 'sqlite' in (db.get('ENGINE') or '').lower():
        raise SystemExit('禁止 SQLite')
    host = (db.get('HOST') or '').strip()
    if not host or host in ('127.0.0.1', 'localhost'):
        raise SystemExit(f'需远程库 HOST={host}')
    print(f'Remote DB {host} / {db.get("NAME")}')


def _resolve(rel_paths):
    for rel in rel_paths:
        p = ASSET_ROOT / rel
        if p.exists() and p.stat().st_size > 5000:
            return p
    return None


def _cover_crop(img: Image.Image, size):
    """Center-crop to target aspect, then resize."""
    tw, th = size
    img = img.convert('RGB')
    sw, sh = img.size
    target_ratio = tw / th
    src_ratio = sw / sh
    if src_ratio > target_ratio:
        new_w = int(sh * target_ratio)
        left = (sw - new_w) // 2
        img = img.crop((left, 0, left + new_w, sh))
    else:
        new_h = int(sw / target_ratio)
        top = (sh - new_h) // 2
        img = img.crop((0, top, sw, top + new_h))
    return img.resize(size, Image.Resampling.LANCZOS)


def _to_jpeg_bytes(img, quality=88):
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=quality, optimize=True)
    return buf.getvalue()


def _upload(key_prefix, name, img, dry_run=False):
    body = _to_jpeg_bytes(img)
    key = f'{key_prefix}/{name}-{uuid.uuid4().hex[:10]}.jpg'
    if dry_run:
        print(f'  [dry-run] would upload {key} ({len(body) // 1024}KB)')
        return f'https://dry-run.local/{key}'
    result = upload_image_to_cos(body, key)
    if not result or not result.get('url'):
        raise RuntimeError(f'COS upload failed: {key}')
    return result['url']


def seed_wallpapers(dry_run=False):
    stamp = datetime.utcnow().strftime('%Y%m%d')
    OUT_WP.mkdir(parents=True, exist_ok=True)
    existing = list(
        Wallpaper.objects.filter(is_deleted=False).order_by('-sort_weight', 'id')
    )
    by_weight = {w.sort_weight: w for w in existing}
    created_or_updated = 0
    for key, vip, tags, weight, paths in WALLPAPER_SPECS:
        src = _resolve(paths)
        if not src:
            print(f'  SKIP wallpaper {key}: no source')
            continue
        img = _cover_crop(Image.open(src), (1080, 1920))
        local_out = OUT_WP / f'{key}.jpg'
        img.save(local_out, format='JPEG', quality=88, optimize=True)
        url = _upload(f'selah/wallpapers/{stamp}', key, img, dry_run=dry_run)
        w = by_weight.get(weight)
        if w and not dry_run:
            w.image_url = url
            w.is_vip = vip
            w.tags = tags
            w.status = 1
            w.save(update_fields=['image_url', 'is_vip', 'tags', 'status', 'updated_at'])
            print(f'  update wallpaper #{w.id} {key} <- {src.name} -> {url}')
        elif not dry_run:
            w = Wallpaper.objects.create(
                image_url=url,
                is_vip=vip,
                tags=tags,
                sort_weight=weight,
                is_deleted=False,
                status=1,
            )
            print(f'  create wallpaper #{w.id} {key} <- {src.name} -> {url}')
        else:
            print(f'  dry wallpaper {key} <- {src} ({local_out.stat().st_size // 1024}KB)')
        created_or_updated += 1
    print(f'Wallpapers done: {created_or_updated}')


def seed_topic_covers(dry_run=False):
    stamp = datetime.utcnow().strftime('%Y%m%d')
    OUT_TP.mkdir(parents=True, exist_ok=True)
    topics = list(Topic.objects.filter(is_deleted=False).order_by('-sort_weight', 'id'))
    n = 0
    for t in topics:
        candidates = None
        for name, paths in TOPIC_COVERS.items():
            if name.lower() in (t.name or '').lower():
                candidates = paths
                break
        if not candidates:
            candidates = ['figma_themes/t02.jpeg', 'web/peace.jpg']
        src = _resolve(candidates)
        if not src:
            print(f'  SKIP topic {t.name}: no source')
            continue
        img = _cover_crop(Image.open(src), (800, 1000))
        slug = ''.join(c if c.isalnum() else '-' for c in (t.name or 'topic'))[:32].strip('-') or 'topic'
        local_out = OUT_TP / f'{slug}.jpg'
        img.save(local_out, format='JPEG', quality=88, optimize=True)
        url = _upload(f'selah/topics/{stamp}', slug, img, dry_run=dry_run)
        if not dry_run:
            t.cover_url = url
            t.save(update_fields=['cover_url', 'updated_at'])
            print(f'  topic #{t.id} {t.name} <- {src.name} -> {url}')
        else:
            print(f'  dry topic {t.name} <- {src} ({local_out.stat().st_size // 1024}KB)')
        n += 1
    print(f'Topic covers done: {n}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    _assert_remote()
    if not ASSET_ROOT.exists():
        raise SystemExit(f'Missing asset root: {ASSET_ROOT}')
    print(f'Assets: {ASSET_ROOT}')
    seed_wallpapers(dry_run=args.dry_run)
    seed_topic_covers(dry_run=args.dry_run)
    if not args.dry_run:
        free = Wallpaper.objects.filter(is_deleted=False, is_vip=False, status=1).count()
        print(f'Done. free_wallpapers={free} total_wp={Wallpaper.objects.filter(is_deleted=False).count()} '
              f'topics_with_cover={Topic.objects.filter(is_deleted=False).exclude(cover_url="").count()}')


if __name__ == '__main__':
    main()
