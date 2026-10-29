#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
删除远程 picsum 占位图，生成 Selah 静态图并上传腾讯云 COS，写回 image_url / cover_url。

用法：
  ./uivsbe_env/bin/python scripts/replace_picsum_assets.py
"""
import io
import os
import sys
import uuid
from datetime import datetime

import django

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from models.models import Topic, User, Wallpaper
from tools.uploader_data import upload_image_to_cos

# (key, is_vip, tags, sort_weight, top_rgb, bottom_rgb)
WALLPAPER_SPECS = [
    ('dawn', False, ['calm', 'sunrise'], 100, (255, 214, 186), (140, 168, 196)),
    ('mist', False, ['calm'], 95, (210, 220, 228), (120, 140, 156)),
    ('sand', False, ['warm'], 90, (236, 220, 196), (168, 140, 112)),
    ('ocean', False, ['ocean'], 85, (168, 206, 220), (46, 90, 120)),
    ('forest', False, ['forest'], 80, (180, 204, 176), (52, 88, 68)),
    ('meadow', False, ['meadow'], 75, (210, 224, 180), (96, 132, 88)),
    ('sky', False, ['sky'], 70, (196, 220, 240), (88, 132, 176)),
    ('soft', False, ['soft'], 65, (232, 228, 236), (160, 152, 168)),
    ('minimal', False, ['minimal'], 60, (244, 242, 238), (200, 196, 188)),
    ('dusk', False, ['calm', 'nature'], 55, (220, 176, 156), (72, 84, 120)),
    ('night', True, ['premium', 'night'], 50, (48, 56, 88), (16, 20, 40)),
    ('gold', True, ['premium', 'gold'], 45, (236, 208, 152), (120, 88, 48)),
    ('deep', True, ['premium', 'abstract'], 40, (72, 96, 112), (28, 40, 56)),
]

# (match_name_substr_or_exact, top, bottom)
TOPIC_COVER_COLORS = {
    'Peace': ((196, 214, 220), (96, 128, 148)),
    'Prayer': ((220, 208, 196), (120, 100, 84)),
    'Hope': ((232, 214, 176), (168, 132, 88)),
    'Courage': ((200, 176, 188), (96, 72, 104)),
    'Healing': ((188, 212, 196), (72, 112, 96)),
    'Family': ((236, 216, 200), (148, 112, 96)),
    'Work': ((196, 204, 212), (88, 104, 120)),
    'Advent': ((188, 200, 220), (64, 80, 120)),
}


def _assert_remote():
    db = settings.DATABASES['default']
    if 'sqlite' in (db.get('ENGINE') or '').lower():
        raise SystemExit('禁止 SQLite')
    host = (db.get('HOST') or '').strip()
    if not host or host in ('127.0.0.1', 'localhost'):
        raise SystemExit(f'需远程库 HOST={host}')
    print(f'Remote DB {host} / {db.get("NAME")}')


def _font(size):
    for path in (
        '/System/Library/Fonts/Supplemental/Georgia.ttf',
        '/System/Library/Fonts/Supplemental/Times New Roman.ttf',
        '/Library/Fonts/Arial.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf',
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _gradient(size, top_rgb, bottom_rgb):
    w, h = size
    img = Image.new('RGB', size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t)
        g = int(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t)
        b = int(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def _to_jpeg_bytes(img, quality=88):
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=quality, optimize=True)
    return buf.getvalue()


def _upload(key_prefix, name, img):
    body = _to_jpeg_bytes(img)
    key = f'{key_prefix}/{name}-{uuid.uuid4().hex[:10]}.jpg'
    result = upload_image_to_cos(body, key)
    if not result or not result.get('url'):
        raise RuntimeError(f'COS upload failed: {key}')
    return result['url']


def make_wallpaper(key, top, bottom):
    img = _gradient((1080, 1920), top, bottom)
    draw = ImageDraw.Draw(img)
    # soft vignette bands
    for i, alpha in enumerate((18, 12, 8)):
        pad = 40 + i * 30
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle([pad, pad, 1080 - pad, 1920 - pad], outline=(255, 255, 255, alpha), width=2)
        img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
        draw = ImageDraw.Draw(img)
    title = _font(72)
    sub = _font(28)
    draw.text((1080 // 2, 820), 'Selah', fill=(255, 255, 255), font=title, anchor='mm')
    draw.text((1080 // 2, 900), key, fill=(255, 255, 255, 180), font=sub, anchor='mm')
    return img


def make_topic_cover(name, top, bottom):
    img = _gradient((800, 600), top, bottom)
    draw = ImageDraw.Draw(img)
    font = _font(48)
    draw.text((400, 300), name[:18], fill=(255, 255, 255), font=font, anchor='mm')
    return img


def delete_picsum():
    wp_qs = Wallpaper.objects.filter(image_url__icontains='picsum')
    ids = list(wp_qs.values_list('id', flat=True))
    n_users = User.objects.filter(wallpaper_id__in=ids).update(wallpaper_id=None)
    deleted, _ = wp_qs.delete()
    topic_n = Topic.objects.filter(cover_url__icontains='picsum').update(cover_url='')
    print(f'Deleted picsum wallpapers={deleted}, cleared user wallpaper refs={n_users}, topic covers cleared={topic_n}')


def seed_wallpapers():
    stamp = datetime.utcnow().strftime('%Y%m%d')
    created = []
    for key, vip, tags, weight, top, bottom in WALLPAPER_SPECS:
        img = make_wallpaper(key, top, bottom)
        url = _upload(f'selah/wallpapers/{stamp}', key, img)
        w = Wallpaper.objects.create(
            image_url=url,
            is_vip=vip,
            tags=tags,
            sort_weight=weight,
            is_deleted=False,
            status=1,
        )
        created.append(w)
        print(f'  wallpaper #{w.id} {key} -> {url}')
    print(f'Wallpapers created: {len(created)}')
    return created


def seed_topic_covers():
    stamp = datetime.utcnow().strftime('%Y%m%d')
    topics = list(Topic.objects.filter(is_deleted=False).order_by('-sort_weight', 'id'))
    n = 0
    for t in topics:
        colors = None
        for name, rgb in TOPIC_COVER_COLORS.items():
            if name.lower() in (t.name or '').lower():
                colors = rgb
                break
        if not colors:
            colors = ((200, 208, 216), (96, 112, 128))
        img = make_topic_cover(t.name or 'Topic', colors[0], colors[1])
        slug = ''.join(c if c.isalnum() else '-' for c in (t.name or 'topic'))[:32].strip('-') or 'topic'
        url = _upload(f'selah/topics/{stamp}', slug, img)
        t.cover_url = url
        t.save(update_fields=['cover_url', 'updated_at'])
        n += 1
        print(f'  topic #{t.id} {t.name} -> {url}')
    print(f'Topic covers updated: {n}')


def main():
    _assert_remote()
    delete_picsum()
    seed_wallpapers()
    seed_topic_covers()
    left = Wallpaper.objects.filter(image_url__icontains='picsum').count()
    left_t = Topic.objects.filter(cover_url__icontains='picsum').count()
    print(f'Done. remaining picsum wallpapers={left} topics={left_t} total_wp={Wallpaper.objects.count()}')


if __name__ == '__main__':
    main()
