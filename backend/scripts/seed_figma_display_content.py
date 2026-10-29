#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
从 Figma 导出图 / 文案补齐展示所需测试内容（远程 PostgreSQL）。

- AiQuickPrompt：对齐主带 AI 空态建议文案（25:4902）
- Topic：无封面时用 Doc/design/assets_import/figma_themes 上传 COS；必要时新增主题
- Wallpaper：条数不足时用 figma_wallpaper / figma_themes 补齐
- Quote：某 Topic 下语录 < 4 时补英文测试语录；冷启池顶到 40
- OnboardingQuestion：补 age_range（Figma 25:5966）
- WelcomeScreen：写入 Figma 欢迎主视觉（25:5706）
- InterstitialScreen：写入 Figma 教育夹层文案（136:*；样式由 App 还原，不配整页截图）

用法：
  ./uivsbe_env/bin/python scripts/seed_figma_display_content.py
  ./uivsbe_env/bin/python scripts/seed_figma_display_content.py --dry-run
  ./uivsbe_env/bin/python scripts/seed_figma_display_content.py --only interstitials
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

from django.conf import settings

from models.models import (
    AiQuickPrompt,
    InterstitialScreen,
    OnboardingQuestion,
    Quote,
    Topic,
    Wallpaper,
    WelcomeScreen,
)
from tools.uploader_data import upload_image_to_cos

ASSET_ROOT = Path(__file__).resolve().parents[3] / 'Doc' / 'design' / 'assets_import'
FIGMA_TH = ASSET_ROOT / 'figma_themes'
FIGMA_WP = ASSET_ROOT / 'figma_wallpaper'
FIGMA_OUT = ASSET_ROOT / 'out'
FIGMA_INT = FIGMA_OUT / 'interstitials'
MOBILE_UI = Path(__file__).resolve().parents[2] / 'frontend' / 'mobile' / 'static' / 'ui'

# Figma 教育夹层文案（136:*）；样式由 App 本地还原，不配整页截图
FIGMA_INTERSTITIALS = [
    {
        'kind': 'personalize_intro',
        'figma': '136:22940',
        'title': 'Personalize the app by sharing a few details about yourself',
        'subtitle': '',
        'cta_text': 'Continue',
        'sort_order': 10,
    },
    {
        'kind': 'prefs_intro',
        'figma': '136:22960',
        'title': 'Set up the app according to your preferences',
        'subtitle': '',
        'cta_text': 'Continue',
        'sort_order': 20,
    },
    {
        'kind': 'push_nudge',
        'figma': '136:21745',
        'title': "Don't miss your daily verses!",
        'subtitle': "Open settings to allow notifications, it'll have a big impact in your life",
        'cta_text': 'Go to settings',
        'sort_order': 30,
    },
    {
        'kind': 'devotion_custom',
        'figma': '136:21811',
        'title': 'Customize the app for your spiritual journey',
        'subtitle': '',
        'cta_text': 'Continue',
        'sort_order': 40,
    },
    {
        'kind': 'value_edu',
        'figma': '136:22652',
        'title': 'Get hourly verses, quotes, and prayers to stay close to God throughout the day',
        'subtitle': '',
        'cta_text': 'Continue',
        'sort_order': 50,
    },
    {
        'kind': 'trial_intro',
        'figma': '136:22689',
        'title': 'We offer 3 days of Premium access for free, just for you',
        'subtitle': '',
        'cta_text': 'Continue',
        'sort_order': 60,
    },
]

# Figma 25:4902 空态建议（英文为主带验收语言）
FIGMA_PROMPTS = [
    ('Get guidance', 'Get guidance for what I’m facing today.', 10),
    ('Find a Bible verse for my situation', 'Find a Bible verse for my situation.', 20),
    ('Write a prayer for me', 'Write a prayer for me.', 30),
    ('Explain a Bible verse', 'Explain a Bible verse in your own words.', 40),
]

SAMPLE_QUOTES = [
    'Be still, and know that I am God.',
    'The Lord is my shepherd; I shall not want.',
    'I can do all things through Christ who strengthens me.',
    'Cast all your anxiety on Him because He cares for you.',
    'Let your light shine before others.',
    'Peace I leave with you; my peace I give you.',
    'Trust in the Lord with all your heart.',
    'The joy of the Lord is your strength.',
    'Come to me, all who labor and are heavy laden.',
    'His mercies are new every morning.',
]

EXTRA_TOPICS = [
    ('Gratitude', 'calm', 'Practice thankfulness each day.'),
    ('Courage', 'growth', 'Stand firm when fear rises.'),
    ('Rest', 'calm', 'Find quiet in God’s presence.'),
    ('Wisdom', 'faith', 'Seek understanding and light.'),
]

AGE_OPTIONS = [
    {'value': 'under_18', 'label': 'Under 18'},
    {'value': '18_24', 'label': '18-24'},
    {'value': '25_34', 'label': '25-34'},
    {'value': '35_44', 'label': '35-44'},
    {'value': '45_54', 'label': '45-54'},
    {'value': '55_plus', 'label': '55+'},
]


def assert_remote():
    db = settings.DATABASES['default']
    if 'sqlite' in (db.get('ENGINE') or '').lower():
        raise SystemExit('禁止 SQLite')
    host = (db.get('HOST') or '').strip()
    if not host or host in ('127.0.0.1', 'localhost'):
        raise SystemExit(f'需远程库 HOST={host}')
    print(f'Remote DB {host} / {db.get("NAME")}')


def list_images(folder: Path):
    if not folder.exists():
        return []
    return sorted(
        [p for p in folder.iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') and p.stat().st_size > 3000],
        key=lambda p: p.name,
    )


def upload_local(path: Path, prefix: str, dry: bool):
    body = path.read_bytes()
    key = f'selah/seed/{prefix}/{uuid.uuid4().hex}{path.suffix.lower()}'
    if dry:
        print(f'  [dry] would upload {path} ({len(body) // 1024}KB)')
        return f'https://example.invalid/{key}'
    result = upload_image_to_cos(body, key)
    if not result or not result.get('url'):
        raise RuntimeError(f'COS upload failed: {key}')
    return result['url']


def seed_prompts(dry: bool):
    print('== AiQuickPrompt (Figma 25:4902) ==')
    if not dry:
        AiQuickPrompt.objects.filter(language__in=['', 'en']).update(enabled=False)
    for title, prompt, order in FIGMA_PROMPTS:
        obj = AiQuickPrompt.objects.filter(title=title, language='en').first()
        if obj:
            print(f'  update #{obj.id} {title}')
            if not dry:
                obj.prompt = prompt
                obj.sort_order = order
                obj.enabled = True
                obj.language = 'en'
                obj.save(update_fields=['prompt', 'sort_order', 'enabled', 'language'])
        else:
            print(f'  create {title}')
            if not dry:
                AiQuickPrompt.objects.create(
                    title=title,
                    prompt=prompt,
                    sort_order=order,
                    enabled=True,
                    language='en',
                )


def seed_extra_topics(dry: bool):
    print('== Extra topics ==')
    imgs = list_images(FIGMA_TH)
    n = Topic.objects.filter(is_deleted=False, status=1).count()
    need = max(0, 12 - n)
    if need <= 0:
        print(f'  ok count={n}')
        return
    created = 0
    for name, cat, desc in EXTRA_TOPICS:
        if created >= need:
            break
        if Topic.objects.filter(name=name, is_deleted=False).exists():
            continue
        img = imgs[created % len(imgs)] if imgs else None
        url = upload_local(img, 'topics', dry) if img else ''
        print(f'  create {name}')
        if not dry:
            Topic.objects.create(
                name=name,
                description=desc,
                category=cat,
                cover_url=url,
                sort_weight=20 - created,
                status=1,
            )
        created += 1


def seed_topic_covers(dry: bool):
    print('== Topic covers ==')
    imgs = list_images(FIGMA_TH)
    if not imgs:
        print('  no figma_themes images')
        return
    topics = list(Topic.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', 'id'))
    for i, t in enumerate(topics):
        if (t.cover_url or '').strip():
            continue
        img = imgs[i % len(imgs)]
        url = upload_local(img, 'topics', dry)
        print(f'  #{t.id} {t.name} <- {img.name}')
        if not dry:
            t.cover_url = url
            t.save(update_fields=['cover_url', 'updated_at'])


def seed_wallpapers(dry: bool):
    print('== Wallpapers ==')
    n = Wallpaper.objects.filter(is_deleted=False, status=1).count()
    need = max(0, 16 - n)
    if need <= 0:
        print(f'  ok count={n}')
        return
    imgs = list_images(FIGMA_WP) or list_images(FIGMA_TH)
    for i in range(need):
        img = imgs[i % len(imgs)]
        url = upload_local(img, 'wallpapers', dry)
        print(f'  add {img.name}')
        if not dry:
            Wallpaper.objects.create(
                image_url=url,
                is_vip=(i % 4 == 0),
                tags=['figma', 'seed'],
                sort_weight=50 - i,
                status=1,
            )


def seed_topic_quotes(dry: bool):
    print('== Topic quotes min-fill ==')
    for t in Topic.objects.filter(is_deleted=False, status=1):
        cnt = Quote.objects.filter(is_deleted=False, status=1, topic_id=t.id).count()
        need = max(0, 4 - cnt)
        if need <= 0:
            continue
        print(f'  #{t.id} {t.name}: +{need}')
        if dry:
            continue
        for j in range(need):
            Quote.objects.create(
                content=SAMPLE_QUOTES[(cnt + j) % len(SAMPLE_QUOTES)],
                language='en',
                topic_id=t.id,
                interest_tags=['peace'],
                sort_weight=10 + j,
                status=1,
                in_cold_start_pool=True,
            )


def seed_cold_pool(dry: bool):
    print('== Cold-start quote pool ==')
    n = Quote.objects.filter(is_deleted=False, status=1, in_cold_start_pool=True).count()
    need = max(0, 40 - n)
    if need <= 0:
        print(f'  ok count={n}')
        return
    print(f'  +{need}')
    if dry:
        return
    topics = list(Topic.objects.filter(is_deleted=False, status=1).order_by('id'))
    for j in range(need):
        tid = topics[j % len(topics)].id if topics else None
        Quote.objects.create(
            content=SAMPLE_QUOTES[j % len(SAMPLE_QUOTES)] + f' ({j + 1})',
            language='en',
            app_key='selah',
            topic_id=tid,
            interest_tags=['peace', 'hope'],
            sort_weight=5,
            status=1,
            in_cold_start_pool=True,
        )


def seed_onboarding_age(dry: bool):
    print('== Onboarding age (Figma 25:5966) ==')
    obj = OnboardingQuestion.objects.filter(
        question_key='age_range', language='en', app_key='selah',
    ).first()
    if not obj:
        obj = OnboardingQuestion.objects.filter(
            question_key='age_range', language='en', app_key='',
        ).first()
    if obj:
        print(f'  update #{obj.id}')
        if not dry:
            obj.title = 'How old are you?'
            obj.options = AGE_OPTIONS
            obj.sort_order = 0
            obj.enabled = True
            obj.app_key = 'selah'
            obj.save(update_fields=['title', 'options', 'sort_order', 'enabled', 'app_key'])
    else:
        print('  create age_range')
        if not dry:
            OnboardingQuestion.objects.create(
                question_key='age_range',
                title='How old are you?',
                options=AGE_OPTIONS,
                sort_order=0,
                enabled=True,
                app_key='selah',
                language='en',
                platform='',
            )


def seed_welcome(dry: bool):
    print('== WelcomeScreen (Figma 25:5706) ==')
    candidates = [
        MOBILE_UI / 'welcome-mist.jpg',
        MOBILE_UI / 'welcome-mist.png',
        MOBILE_UI / 'welcome-hero.jpg',
        FIGMA_OUT / 'welcome-raw1.jpeg',
        MOBILE_UI / 'welcome-export.png',
        MOBILE_UI / 'welcome-hero.png',
    ]
    img = next((p for p in candidates if p.exists() and p.stat().st_size > 3000), None)
    url = upload_local(img, 'welcome', dry) if img else ''
    qs = WelcomeScreen.objects.filter(enabled=True, app_key='selah').order_by('sort_order', 'id')
    obj = qs.first()
    if not obj:
        obj = WelcomeScreen.objects.filter(enabled=True, app_key='').order_by('sort_order', 'id').first()
    if obj:
        print(f'  update #{obj.id} img={bool(url)} src={img}')
        if not dry:
            obj.title = 'Selah'
            obj.subtitle = 'Grow closer to God'
            obj.cta_text = 'Continue'
            if url:
                obj.image_url = url
            obj.version = max(int(obj.version or 1), 3)
            obj.enabled = True
            obj.app_key = 'selah'
            obj.save(update_fields=[
                'title', 'subtitle', 'cta_text', 'image_url', 'version', 'enabled', 'app_key', 'updated_at',
            ])
    else:
        print('  create welcome')
        if not dry:
            WelcomeScreen.objects.create(
                title='Selah',
                subtitle='Grow closer to God',
                cta_text='Continue',
                image_url=url,
                version=3,
                sort_order=0,
                enabled=True,
                app_key='selah',
                language='',
                platform='',
            )


def seed_interstitials(dry: bool):
    print('== InterstitialScreen (Figma 136:* 文案；不上传整页截图；app_key=selah) ==')
    for row in FIGMA_INTERSTITIALS:
        kind = row['kind']
        obj = InterstitialScreen.objects.filter(
            kind=kind, app_key='selah', language='', platform='',
        ).first()
        if not obj:
            obj = InterstitialScreen.objects.filter(
                kind=kind, app_key='', language='', platform='',
            ).first()
        if not obj:
            obj = InterstitialScreen.objects.filter(kind=kind, enabled=True).order_by('id').first()
        if obj:
            print(f'  update #{obj.id} {kind} ({row["figma"]}) clear_img')
            if not dry:
                obj.title = row['title']
                obj.subtitle = row['subtitle']
                obj.cta_text = row['cta_text']
                # 样式由 App 按 Figma 本地还原；运营 image 仅可选小插画，清空旧截图
                obj.image_url = ''
                obj.version = max(int(obj.version or 1), 2)
                obj.sort_order = row['sort_order']
                obj.enabled = True
                obj.app_key = 'selah'
                obj.language = ''
                obj.platform = ''
                obj.save()
        else:
            print(f'  create {kind} ({row["figma"]})')
            if not dry:
                InterstitialScreen.objects.create(
                    kind=kind,
                    title=row['title'],
                    subtitle=row['subtitle'],
                    cta_text=row['cta_text'],
                    image_url='',
                    version=2,
                    sort_order=row['sort_order'],
                    enabled=True,
                    app_key='selah',
                    language='',
                    platform='',
                )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument(
        '--only',
        choices=['all', 'interstitials', 'welcome', 'prompts', 'topics', 'wallpapers', 'quotes', 'onboarding'],
        default='all',
    )
    args = ap.parse_args()
    assert_remote()
    only = args.only
    if only in ('all', 'prompts'):
        seed_prompts(args.dry_run)
    if only in ('all', 'topics'):
        seed_extra_topics(args.dry_run)
        seed_topic_covers(args.dry_run)
    if only in ('all', 'wallpapers'):
        seed_wallpapers(args.dry_run)
    if only in ('all', 'quotes'):
        seed_topic_quotes(args.dry_run)
        seed_cold_pool(args.dry_run)
    if only in ('all', 'onboarding'):
        seed_onboarding_age(args.dry_run)
    if only in ('all', 'welcome'):
        seed_welcome(args.dry_run)
    if only in ('all', 'interstitials'):
        seed_interstitials(args.dry_run)
    print('done')


if __name__ == '__main__':
    main()
