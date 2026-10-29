#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
Selah 全量测试数据填充（可重复执行）。

覆盖：3.0 Quiz/Icons、语录/主题/壁纸(含 video)、Welcome/夹层、肯定语模板、
Support/Compose 配置键、测试用户 widget_prefs / selected_icon_key / 肯定语。

用法：
  cd backend
  ./uivsbe_env/bin/python scripts/seed_selah_test_full.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import django

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from models.models import (
    Affirmation,
    AffirmationTemplate,
    AppIcon,
    InterstitialScreen,
    Quiz,
    QuizQuestion,
    Quote,
    Topic,
    User,
    Wallpaper,
    WelcomeScreen,
)
from tools.membership_products import ensure_default_products
from tools.selah_quota import AppConfig, ensure_default_configs
from tools.selah_virtual_seed import (
    ensure_virtual_catalog,
    ensure_virtual_interstitials,
    ensure_virtual_onboarding,
)

# 本地静态资源 / 公开样例视频（测试用，非生产 CDN）
SAMPLE_VIDEO = 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerJoyrides.mp4'
SAMPLE_POSTER = 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/images/ForBiggerJoyrides.jpg'

QUOTES = [
    ('Be still, and know that I am God.', 'Psalm 46:10', True, ['peace'], False),
    ('The Lord is my shepherd; I shall not want.', 'Psalm 23:1', True, ['comfort'], False),
    ('I can do all things through Christ who strengthens me.', 'Philippians 4:13', True, ['strength'], False),
    ('Love is patient, love is kind.', '1 Corinthians 13:4', True, ['love'], False),
    ('Do not be anxious about tomorrow.', 'Matthew 6:34', True, ['peace'], False),
    ('Give thanks in all circumstances.', '1 Thessalonians 5:18', False, ['gratitude'], False),
    ('Faith is confidence in what we hope for.', 'Hebrews 11:1', True, ['faith'], False),
    ('Blessed are the peacemakers.', 'Matthew 5:9', False, ['peace'], False),
    ('In the beginning was the Word.', 'John 1:1', True, ['faith'], False),
    ('Cast all your anxiety on him because he cares for you.', '1 Peter 5:7', True, ['comfort'], False),
    ('VIP: His mercies are new every morning.', 'Lamentations 3:23', True, ['hope'], True),
    ('VIP: Rejoice always, pray continually.', '1 Thessalonians 5:16-17', False, ['prayer'], True),
    ('The joy of the Lord is your strength.', 'Nehemiah 8:10', True, ['joy'], False),
    ('Come to me, all who are weary.', 'Matthew 11:28', True, ['comfort'], False),
    ('Walk by faith, not by sight.', '2 Corinthians 5:7', False, ['faith'], False),
]

TOPICS = [
    ('Peace', 'Find quiet in His presence', 'peace', False, 100),
    ('Hope', 'Light for hard days', 'hope', False, 90),
    ('Strength', 'Courage for the road', 'strength', False, 80),
    ('Love', 'Love that never fails', 'love', False, 70),
    ('Gratitude', 'Give thanks always', 'gratitude', False, 60),
    ('VIP Deep Dive', 'Member themes', 'faith', True, 50),
]

WALLPAPERS_STATIC = [
    ('/static/ui/wall-cream.png', ['cream', 'soft'], False, 100),
    ('/static/ui/wall-lavender.png', ['lavender'], False, 90),
    ('/static/ui/wall-dawn.png', ['dawn'], False, 80),
    ('/static/ui/wall-vip-gold.png', ['gold', 'vip'], True, 70),
]

QUIZ_EXTRA = {
    'title': 'Selah Daily Reflection',
    'subtitle': 'A second quiz pack for QA',
    'questions': [
        ('What should we do without ceasing?', [('a', 'Pray'), ('b', 'Worry'), ('c', 'Argue')], 'a'),
        ('God is love — from which book?', [('a', '1 John'), ('b', 'Jonah'), ('c', 'Ruth')], 'a'),
        ('Blessed are the merciful, for they will be…', [('a', 'shown mercy'), ('b', 'forgotten'), ('c', 'ignored')], 'a'),
        ('Your word is a lamp to my…', [('a', 'feet'), ('b', 'fears'), ('c', 'pride')], 'a'),
        ('Taste and see that the Lord is…', [('a', 'good'), ('b', 'distant'), ('c', 'angry')], 'a'),
        ('He makes me lie down in…', [('a', 'green pastures'), ('b', 'storms'), ('c', 'deserts')], 'a'),
        ('Seek first his kingdom and his…', [('a', 'righteousness'), ('b', 'wealth'), ('c', 'fame')], 'a'),
        ('A gentle answer turns away…', [('a', 'wrath'), ('b', 'hope'), ('c', 'joy')], 'a'),
        ('The Lord is close to the…', [('a', 'brokenhearted'), ('b', 'proud'), ('c', 'cruel')], 'a'),
        ('Let everything that has breath…', [('a', 'praise the Lord'), ('b', 'fear tomorrow'), ('c', 'stay silent')], 'a'),
    ],
}


def upsert_config(key: str, value, remark: str = '') -> None:
    qs = AppConfig.objects.filter(key=key).order_by('id')
    obj = qs.first()
    if not obj:
        AppConfig.objects.create(key=key, value=value, remark=remark)
        return
    obj.value = value
    if remark:
        obj.remark = remark
    obj.save(update_fields=['value', 'remark', 'updated_at'] if hasattr(obj, 'updated_at') else ['value', 'remark'])
    # collapse accidental duplicates
    qs.exclude(id=obj.id).delete()


def seed_configs() -> None:
    ensure_default_configs()
    for k, v, r in [
        ('compose_enabled', True, '3.0 compose'),
        ('quiz_enabled', True, '3.0 quiz'),
        ('widget_enabled', True, '3.0 widget'),
        ('app_icon_enabled', True, '3.0 icons'),
        ('dynamic_wallpaper_enabled', True, '3.0 video wallpaper'),
        ('compose_entry_label', 'Create', 'Profile compose CTA'),
        ('url_rate', 'https://selah.app/rate', 'Support Selah rate'),
        ('url_download', 'https://selah.app/download', 'Share app'),
        ('url_instagram', 'https://instagram.com/selah', 'Follow'),
        ('url_help', 'https://selah.app/help', 'Help'),
        ('support_tips', 'Thank you for supporting Selah.', 'Support tips'),
    ]:
        upsert_config(k, v, r)
    print('configs ok')


def seed_quotes_topics() -> dict:
    topic_ids = {}
    for name, desc, cat, vip, weight in TOPICS:
        t, _ = Topic.objects.get_or_create(
            name=name,
            app_key='selah',
            language='en',
            platform='',
            defaults={
                'description': desc,
                'category': cat,
                'is_vip': vip,
                'sort_weight': weight,
                'status': 1,
                'cover_url': '/static/ui/topic-cover.png',
            },
        )
        t.is_deleted = False
        t.status = 1
        t.is_vip = vip
        t.save()
        topic_ids[name] = t.id

    for content, source, cold, tags, vip in QUOTES:
        topic_name = 'Peace' if 'peace' in tags else ('Hope' if 'hope' in tags else 'Faith' if 'faith' in tags else 'Peace')
        if topic_name not in topic_ids:
            topic_name = next(iter(topic_ids))
        q, created = Quote.objects.get_or_create(
            content=content,
            language='en',
            app_key='selah',
            platform='',
            defaults={
                'source_ref': source,
                'interest_tags': tags,
                'faith_tags': ['christian'],
                'in_cold_start_pool': cold,
                'is_vip': vip,
                'status': 1,
                'sort_weight': 50,
                'topic_id': topic_ids.get(topic_name),
            },
        )
        if not created:
            q.source_ref = source
            q.is_deleted = False
            q.status = 1
            q.is_vip = vip
            q.topic_id = topic_ids.get(topic_name)
            q.save()
    print('quotes', Quote.objects.filter(app_key='selah', is_deleted=False).count(), 'topics', len(topic_ids))
    return topic_ids


def seed_wallpapers() -> None:
    for url, tags, vip, weight in WALLPAPERS_STATIC:
        w, _ = Wallpaper.objects.get_or_create(
            image_url=url,
            app_key='selah',
            language='',
            platform='',
            defaults={
                'media_type': 'static',
                'tags': tags,
                'is_vip': vip,
                'sort_weight': weight,
                'status': 1,
            },
        )
        w.is_deleted = False
        w.status = 1
        w.media_type = 'static'
        w.save()

    for i, (title_tag, vip) in enumerate([('dynamic_joy', False), ('dynamic_vip', True)], start=1):
        existing = Wallpaper.objects.filter(
            app_key='selah', media_type='video', is_deleted=False
        )
        hit = None
        for cand in existing:
            tags = cand.tags or []
            if title_tag in tags:
                hit = cand
                break
        if hit:
            w = hit
        else:
            w = Wallpaper.objects.create(
                image_url=SAMPLE_POSTER,
                video_url=SAMPLE_VIDEO,
                poster_url=SAMPLE_POSTER,
                media_type='video',
                duration_sec=15,
                tags=['dynamic', title_tag],
                is_vip=vip,
                sort_weight=40 - i,
                status=1,
                app_key='selah',
                language='',
                platform='',
            )
        w.media_type = 'video'
        w.poster_url = SAMPLE_POSTER
        w.video_url = SAMPLE_VIDEO
        w.duration_sec = 15
        w.is_deleted = False
        w.status = 1
        w.is_vip = vip
        w.tags = ['dynamic', title_tag]
        w.save()
    print(
        'wallpapers static',
        Wallpaper.objects.filter(media_type='static', is_deleted=False).count(),
        'video',
        Wallpaper.objects.filter(media_type='video', is_deleted=False).count(),
    )


def seed_quiz_icons() -> None:
    # inline 3.0 quiz/icons to avoid package import path issues
    from scripts import seed_selah_3_0 as s30

    s30.seed_quiz()
    s30.seed_icons()
    pack = QUIZ_EXTRA
    quiz, _ = Quiz.objects.get_or_create(
        title=pack['title'],
        language='',
        app_key='',
        platform='',
        defaults={'subtitle': pack['subtitle'], 'enabled': True, 'sort_order': 1},
    )
    quiz.is_deleted = False
    quiz.enabled = True
    quiz.save()
    if QuizQuestion.objects.filter(quiz_id=quiz.id).count() < 10:
        for i, (stem, options, correct) in enumerate(pack['questions']):
            QuizQuestion.objects.get_or_create(
                quiz_id=quiz.id,
                stem=stem,
                defaults={
                    'options': [{'key': k, 'text': t} for k, t in options],
                    'correct_key': correct,
                    'sort_order': i,
                    'enabled': True,
                },
            )
    print('quizzes', Quiz.objects.filter(is_deleted=False, enabled=True).count())


def seed_templates_welcome() -> None:
    for i, text in enumerate(
        [
            'I am held by grace today.',
            'God walks with me on this road.',
            'I choose peace over fear.',
            'My heart is thankful.',
            'I rest in His promises.',
        ]
    ):
        AffirmationTemplate.objects.get_or_create(
            content=text,
            app_key='selah',
            language='en',
            platform='',
            defaults={'sort_weight': 100 - i, 'status': 1},
        )

    WelcomeScreen.objects.get_or_create(
        title='Welcome to Selah',
        app_key='selah',
        language='en',
        platform='',
        defaults={
            'subtitle': 'Daily affirmations and gentle devotion',
            'cta_text': 'Continue',
            'version': 1,
            'enabled': True,
            'sort_order': 0,
        },
    )
    for kind, title, sub in [
        ('personalize_intro', 'Make it yours', 'A few questions help us care for you'),
        ('trial_intro', 'Try Selah Premium', '3 days to explore member themes'),
        ('value_education', 'Why Selah', 'Quiet space for Scripture and rest'),
    ]:
        InterstitialScreen.objects.get_or_create(
            kind=kind,
            app_key='selah',
            language='en',
            platform='',
            defaults={
                'title': title,
                'subtitle': sub,
                'cta_text': 'Continue',
                'version': 1,
                'enabled': True,
                'sort_order': 0,
            },
        )
    print('templates/welcome/interstitials ok')


def seed_test_user_state(topic_ids: dict) -> None:
    user, created = User.objects.get_or_create(
        device_id='selah_test_full_device',
        defaults={
            'display_name': 'Selah QA',
            'login_type': 'device',
            'app_key': 'selah',
            'platform': 'ios',
            'system_lang': 'en',
            'widget_prefs': {
                'theme_key': 'lavender',
                'border_key': 'soft',
                'refresh': 'daily',
                'quote_source': 'recommend',
            },
            'selected_icon_key': 'cross_light',
        },
    )
    user.widget_prefs = {
        'theme_key': 'lavender',
        'border_key': 'soft',
        'refresh': 'daily',
        'quote_source': 'recommend',
        'followed_topic_ids': list(topic_ids.values())[:3],
    }
    user.alarm_prefs = {'enabled': True, 'hour': 8, 'minute': 0}
    user.prayer_lock_prefs = {
        'enabled': True,
        'app_ids': ['compose', 'quiz', 'wallpapers'],
        'last_prayer_date': '',
    }
    user.selected_icon_key = 'cross_light'
    user.display_name = user.display_name or 'Selah QA'
    user.save()
    Affirmation.objects.get_or_create(
        user_id=user.id,
        content='I am grateful for today’s quiet mercy.',
        defaults={
            'theme_id': next(iter(topic_ids.values()), None),
            'meta': {'source': 'compose_seed', 'at': datetime.now(timezone.utc).isoformat()},
        },
    )
    print(('created' if created else 'updated'), 'test user', user.id)


def main() -> None:
    ensure_default_products()
    ensure_virtual_catalog()
    ensure_virtual_onboarding()
    ensure_virtual_interstitials()
    seed_configs()
    topic_ids = seed_quotes_topics()
    seed_wallpapers()
    seed_quiz_icons()
    seed_templates_welcome()
    seed_test_user_state(topic_ids)
    print('=== seed_selah_test_full DONE ===')
    print(
        'counts',
        {
            'quote': Quote.objects.filter(is_deleted=False).count(),
            'topic': Topic.objects.filter(is_deleted=False).count(),
            'wallpaper': Wallpaper.objects.filter(is_deleted=False).count(),
            'quiz': Quiz.objects.filter(is_deleted=False).count(),
            'icon': AppIcon.objects.filter(is_deleted=False).count(),
            'tpl': AffirmationTemplate.objects.filter(is_deleted=False).count(),
        },
    )


if __name__ == '__main__':
    main()
