#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Seed Manna ops content from content/manna/ops_seed.py (not Bible chapter bodies).

Bible verse/chapter import stays in seed_bible() via tools.bible_import only.
Idempotent: update_or_create / get_or_create on stable keys.
"""
import os
import sys

import django

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
# content pack lives at content/manna/
CONTENT_ROOT = os.path.join(os.path.dirname(BASE), 'content')
sys.path.insert(0, CONTENT_ROOT)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from datetime import timedelta

from django.db import transaction
from django.utils import timezone as dj_tz

from manna.ops_seed import (  # noqa: E402  (path injected above)
    BADGES,
    DAILY_VERSES,
    DEVOTION_SERIES,
    PRAYERS,
    QUIZ_LEVELS,
    STATS_LINES,
    STORIES,
    TAGS,
    TESTIMONIES,
)
from models.models import (
    AppConfig, BadgeDef, DailyVerse,
    DevotionSeries, DevotionSession, MannaContentTag, MannaHomeCard, MannaQuizLevel,
    MannaQuizQuestion, MannaQuizStory, MannaStatsCopy, MannaTestimony, PrayerContent,
    VerseCardTheme, WelcomeScreen,
)
from tools.bible_import import import_full_text, import_seed_core
from tools.selah_quota import MANNA_APP_KEY, ensure_manna_defaults

APP = MANNA_APP_KEY
# 空串 = 全语言：英文运营包在 zh/es 等 UI 下仍可见（有本地化包后再改具体 language）
LANG = ''

FIGMA_BG = '/static/ui/home/bgs'
HOME_BG_MARK = '/static/ui/home/bgs/'

# Home verse cards keep FIGMA_BG (baked-in copy). Other surfaces use text-free photos.
SCENE_MORNING = [
    '/static/ui/prayer/bg-morning.png',
    '/static/ui/share/bg-field.png',
    '/static/ui/share/bg-path.png',
    '/static/ui/share/bg-beach.png',
]
SCENE_NIGHT = [
    '/static/ui/prayer/bg-night.png',
    '/static/ui/share/bg-heart.png',
    '/static/ui/share/bg-embrace.png',
    '/static/ui/bible/listen/bg-listen.png',
    '/static/ui/home/bg-279-364.png',
]


def figma_prayer_bg(kind, index):
    """Home verse-card art (baked-in copy). index 0-based."""
    night = kind == 'night'
    folder = 'night' if night else 'morning'
    prefix = 'NightPrayer' if night else 'MorningPrayer'
    n = (int(index) % 36) + 1
    name = f'{prefix}.png' if n == 36 else f'{prefix}-{n}.png'
    return f'{FIGMA_BG}/{folder}/{name}'


def scene_bg(kind, index=0):
    """Text-free photo for prayer / covers / share. index 0-based."""
    pool = SCENE_NIGHT if kind == 'night' else SCENE_MORNING
    return pool[int(index) % len(pool)]


def rewrite_home_pool_on_other_surfaces():
    """Prayer / devotion / stories must not reuse home verse-card art."""
    n = 0
    qs = PrayerContent.objects.filter(app_key=APP, image_url__contains=HOME_BG_MARK)
    for i, row in enumerate(qs):
        row.image_url = scene_bg(row.kind, i)
        row.save(update_fields=['image_url'])
        n += 1
    qs = DevotionSeries.objects.filter(app_key=APP, cover_url__contains=HOME_BG_MARK)
    for i, row in enumerate(qs):
        row.cover_url = scene_bg('night' if i % 5 == 0 else 'morning', i)
        row.save(update_fields=['cover_url'])
        n += 1
    qs = MannaQuizStory.objects.filter(app_key=APP, cover_url__contains=HOME_BG_MARK)
    for i, row in enumerate(qs):
        row.cover_url = scene_bg('night' if i % 2 else 'morning', i)
        row.save(update_fields=['cover_url'])
        n += 1
    return n


BADGE_ICON = {
    'first_amen': '/static/ui/me/ico-pray.svg',
    'prayer_dawn': '/static/ui/me/ico-pray.svg',
    'prayer_night': '/static/ui/me/ico-pray.svg',
    'quiz_starter': '/static/ui/me/ico-quiz.svg',
    'devotion_first': '/static/ui/me/ico-devotion.svg',
    'story_seeker': '/static/ui/me/ico-stories-fill.svg',
    'bible_first': '/static/ui/me/ico-bible-fill.svg',
    'amen_ten': '/static/ui/me/ico-pray.svg',
    'devotion_week': '/static/ui/me/ico-devotion.svg',
    'streak_3': '/static/ui/me/ico-calendar.svg',
    'streak_7': '/static/ui/me/ico-calendar.svg',
    'quiz_level3': '/static/ui/me/ico-quiz.svg',
    'quiz_level5': '/static/ui/me/ico-quiz.svg',
    'still_held': '/static/ui/me/ico-badges.svg',
}

DEVOTION_COVER = {
    'Overcoming Anxiety': '/static/ui/devotion/covers/featured-anxiety.png',
    'Learning to Love': '/static/ui/devotion/covers/thumb-love.png',
    'Building the Family': '/static/ui/devotion/covers/thumb-family.png',
    'Rekindling Hope': '/static/ui/devotion/covers/thumb-hope.png',
    'From Fear to Faith': '/static/ui/devotion/covers/thumb-fear.png',
}


def migrate_ops_lang_to_all():
    """把历史 language=en 的 Manna 运营行迁成全语言，避免与新种子键冲突、zh 会话空白。"""
    models = (
        DailyVerse, DevotionSeries, MannaQuizLevel, MannaQuizStory,
        PrayerContent, BadgeDef, MannaTestimony, MannaHomeCard,
        MannaStatsCopy, MannaContentTag,
    )
    for model in models:
        n = model.objects.filter(app_key=APP, language='en').update(language=LANG)
        if n:
            sys.stderr.write(f'migrate {model.__name__} en→all: {n}\n')


def _norm_bible_ref(s):
    return ' '.join(str(s or '').upper().replace('–', '-').split())


def _seed_prayer_copy(row):
    """Explicit pack fields first; else English morning prayer with the same (or range) ref."""
    title = str(row.get('prayer_title') or '').strip()
    body = str(row.get('prayer_body') or '').strip()
    if body:
        return title, body
    ref = _norm_bible_ref(row.get('reference'))
    for p in PRAYERS:
        if p.get('language'):
            continue
        pref = _norm_bible_ref(p.get('reference'))
        if pref == ref or pref.startswith(ref + '-') or ref.startswith(pref + '-'):
            return title, (p.get('body') or '').strip()
    return title, (
        'Father, I receive Your word and ask You to write it on my heart today.\n\n'
        'Where I am anxious, be my peace. Where I am proud, make me humble. '
        'Where I am weary, be my strength.\n\n'
        'Keep me near You. In Jesus’ name, Amen.'
    )


def seed_daily_verses():
    today = dj_tz.now().date()
    for row in DAILY_VERSES:
        offset = int(row.get('day_offset') or 0)
        prayer_title, prayer_body = _seed_prayer_copy(row)
        DailyVerse.objects.update_or_create(
            reference=row['reference'], app_key=APP, language=LANG, platform='',
            defaults={
                'verse_text': row['verse_text'],
                'bible_book': row.get('bible_book') or '',
                'chapter': int(row.get('chapter') or 0),
                'verse_start': int(row.get('verse_start') or 0),
                'verse_end': int(row.get('verse_end') or 0),
                'prayer_title': prayer_title,
                'prayer_body': prayer_body,
                'active_date': today + timedelta(days=offset),
                'sort_order': int(row.get('sort_order') or 0),
                'enabled': True,
                'image_url': row.get('image_url') or figma_prayer_bg('morning', int(row.get('sort_order') or 0)),
                'image_url_night': row.get('image_url_night') or figma_prayer_bg('night', int(row.get('sort_order') or 0)),
                'app_key': APP,
                'language': LANG,
            },
        )
    return len(DAILY_VERSES)


# 画面不重复的分享底图（故意不收录与 beach 同构图的 bg-heart）
VERSECARD_THEMES = (
    {'theme_key': 'field', 'image_url': '/static/ui/share/bg-field.png', 'sort_order': 10, 'is_default': False},
    {'theme_key': 'path', 'image_url': '/static/ui/share/bg-path.png', 'sort_order': 20, 'is_default': False},
    {'theme_key': 'beach', 'image_url': '/static/ui/share/bg-beach.png', 'sort_order': 30, 'is_default': False},
    {'theme_key': 'embrace', 'image_url': '/static/ui/share/bg-embrace.png', 'sort_order': 40, 'is_default': True},
    {'theme_key': 'home', 'image_url': '/static/ui/home/bg-279-364.png', 'sort_order': 50, 'is_default': False},
)


def seed_versecard_themes():
    """Verse card 底栏主题；同范围 theme_key 幂等；禁用心跳重复构图 heart。"""
    n = 0
    for row in VERSECARD_THEMES:
        VerseCardTheme.objects.update_or_create(
            theme_key=row['theme_key'], app_key=APP, language=LANG, platform='',
            defaults={
                'image_url': row['image_url'],
                'sort_order': int(row['sort_order']),
                'enabled': True,
                'is_default': bool(row.get('is_default')),
            },
        )
        n += 1
    # 历史写死列表里的 heart 与 beach 同构图：若曾手工入库则下架
    VerseCardTheme.objects.filter(
        app_key=APP, theme_key='heart',
    ).update(enabled=False)
    return n


def seed_bible():
    """从 content/bible 导入 WEB+KJV（不变：经文章节正文）。

    全量正文：环境变量 MANNA_BIBLE_FULL=1（或传 --full）时导入 WEB 全书（可选 KJV）。
    全量音频：默认 content/bible/audio；仅当文件在仓库外时设 BIBLE_AUDIO_ROOT。
    """
    core = import_seed_core(app_key=APP, language=LANG)
    full_flag = (
        os.getenv('MANNA_BIBLE_FULL', '').strip() in ('1', 'true', 'yes')
        or '--full' in sys.argv
    )
    full_stats = {}
    if full_flag:
        versions = ['WEB']
        if '--kjv' in sys.argv or os.getenv('MANNA_BIBLE_KJV', '').strip() in ('1', 'true', 'yes'):
            versions.append('KJV')
        for ver in versions:
            full_stats[ver] = import_full_text(ver, app_key=APP, language=LANG)
    web_verses = int((core.get('WEB') or {}).get('verses') or 0)
    return core, full_stats, web_verses


def seed_devotion():
    first_series_id = None
    series_count = 0
    session_count = 0
    with transaction.atomic():
        for pack in DEVOTION_SERIES:
            series, _ = DevotionSeries.objects.update_or_create(
                title=pack['title'], app_key=APP, language=LANG, platform='',
                defaults={
                    'pastor_bio': pack.get('pastor_bio') or pack.get('subtitle') or '',
                    'cover_url': pack.get('cover_url') or DEVOTION_COVER.get(pack['title'], ''),
                    'pastor_name': pack.get('pastor_name') or '',
                    'pastor_avatar_url': pack.get('pastor_avatar_url') or '',
                    'tags': pack.get('tags') or [],
                    'session_count': len(pack.get('sessions') or []),
                    'is_vip': bool(pack.get('is_vip')),
                    'enabled': True,
                    'sort_order': int(pack.get('sort_order') or 0),
                },
            )
            series_count += 1
            if first_series_id is None:
                first_series_id = series.id
            for i, sess in enumerate(pack.get('sessions') or []):
                DevotionSession.objects.update_or_create(
                    series_id=series.id, title=sess['title'],
                    defaults={
                        'scripture_ref': sess.get('scripture_ref') or '',
                        'scripture_text': sess.get('scripture_text') or '',
                        'body': sess.get('body') or '',
                        'sort_order': i,
                        'duration_hint_sec': int(sess.get('duration_hint_sec') or 180),
                        'enabled': True,
                    },
                )
                session_count += 1
            series.session_count = DevotionSession.objects.filter(
                series_id=series.id, enabled=True,
            ).count()
            series.save(update_fields=['session_count'])
    return first_series_id, series_count, session_count


def seed_quiz():
    level_ids = {}
    q_total = 0
    with transaction.atomic():
        for pack in QUIZ_LEVELS:
            level, _ = MannaQuizLevel.objects.update_or_create(
                level_no=pack['level_no'], app_key=APP, language=LANG, platform='',
                defaults={
                    'title': pack['title'],
                    'time_limit_sec': int(pack.get('time_limit_sec') or 0),
                    'pass_score': int(pack.get('pass_score') or 1),
                    'reward_manna': int(pack.get('reward_manna') or 0),
                    'fail_penalty': int(pack.get('fail_penalty') or 0),
                    'enabled': True,
                    'sort_order': int(pack.get('sort_order') or 0),
                },
            )
            level_ids[pack['level_no']] = level.id
            for i, q in enumerate(pack.get('questions') or []):
                MannaQuizQuestion.objects.update_or_create(
                    level_id=level.id, stem=q['stem'],
                    defaults={
                        'qtype': q.get('qtype') or 'single',
                        'options': q.get('options') or [],
                        'answer_key': q.get('answer_key') or '',
                        'bible_ref': q.get('bible_ref') or {},
                        'explanation': q.get('explanation') or '',
                        'sort_order': i,
                        'enabled': True,
                    },
                )
                q_total += 1

        story_ids = []
        for pack in STORIES:
            linked_no = pack.get('linked_level_no')
            linked_id = level_ids.get(linked_no) if linked_no else None
            story, _ = MannaQuizStory.objects.update_or_create(
                title=pack['title'], app_key=APP, language=LANG, platform='',
                defaults={
                    'cover_url': pack.get('cover_url') or (
                        '/static/ui/quiz/story-cover-moses.png'
                        if 'Moses' in pack['title']
                        else '/static/ui/quiz/bg-591-2092.png'
                    ),
                    'card_url': pack.get('card_url') or '',
                    'unlock_cost': int(pack.get('unlock_cost') or 0),
                    'knowledge_cards': pack.get('knowledge_cards') or [],
                    'linked_level_id': linked_id,
                    'body': pack.get('body') or '',
                    'enabled': True,
                    'sort_order': int(pack.get('sort_order') or 0),
                },
            )
            story_ids.append(story.id)

    return level_ids, q_total, story_ids


def seed_prayer_badges():
    for row in PRAYERS:
        lang = row.get('language') if 'language' in row else LANG
        plat = row.get('platform') if 'platform' in row else ''
        PrayerContent.objects.update_or_create(
            kind=row['kind'], title=row['title'], app_key=APP,
            language=lang or '', platform=plat or '',
            defaults={
                'body': row.get('body') or '',
                'verse_text': row.get('verse_text') or '',
                'reference': row.get('reference') or '',
                'image_url': row.get('image_url') or scene_bg(row['kind'], int(row.get('sort_order') or 0)),
                'version': int(row.get('version') or 1),
                'enabled': True,
                'sort_order': int(row.get('sort_order') or 0),
            },
        )
    for row in BADGES:
        BadgeDef.objects.update_or_create(
            code=row['code'], app_key=APP, language=LANG, platform='',
            defaults={
                'title': row['title'],
                'icon_url': row.get('icon_url') or BADGE_ICON.get(row['code'], '/static/ui/me/ico-badges.svg'),
                'rule': row.get('rule') or {},
                'enabled': True,
                'sort_order': int(row.get('sort_order') or 0),
            },
        )
    keep = {row['code'] for row in BADGES}
    BadgeDef.objects.filter(app_key=APP).exclude(code__in=keep).update(enabled=False)
    return len(PRAYERS), len(BADGES)


def prune_orphans():
    """Drop thin leftover demo rows not present in the current content pack."""
    keep_series = {p['title'] for p in DEVOTION_SERIES}
    for s in DevotionSeries.objects.filter(app_key=APP, language=LANG).exclude(title__in=keep_series):
        DevotionSession.objects.filter(series_id=s.id).delete()
        s.delete()
    keep_stories = {p['title'] for p in STORIES}
    MannaQuizStory.objects.filter(app_key=APP, language=LANG).exclude(title__in=keep_stories).delete()
    keep_levels = {p['level_no'] for p in QUIZ_LEVELS}
    for lv in MannaQuizLevel.objects.filter(app_key=APP, language=LANG).exclude(level_no__in=keep_levels):
        MannaQuizQuestion.objects.filter(level_id=lv.id).delete()
        lv.delete()
    for pack in QUIZ_LEVELS:
        lv = MannaQuizLevel.objects.filter(
            level_no=pack['level_no'], app_key=APP, language=LANG, platform='',
        ).first()
        if not lv:
            continue
        keep = {q['stem'] for q in (pack.get('questions') or [])}
        MannaQuizQuestion.objects.filter(level_id=lv.id).exclude(stem__in=keep).delete()
    keep_refs = {p['reference'] for p in DAILY_VERSES}
    DailyVerse.objects.filter(app_key=APP).exclude(
        reference__in=keep_refs, language=LANG, platform='',
    ).delete()
    keep_home = {'Daily Quiz', 'Daily Devotion', 'Testimony', 'Bible Stories'}
    MannaHomeCard.objects.filter(app_key=APP).exclude(title__in=keep_home).delete()
    MannaHomeCard.objects.filter(title='~').delete()
    keep_prayer = {(p['kind'], p['title'], p.get('language') or '') for p in PRAYERS}
    for row in PrayerContent.objects.filter(app_key=APP):
        key = (row.kind, row.title, row.language or '')
        if key not in keep_prayer:
            row.delete()


def seed_home_2_0(series_id, level_ids, story_ids):
    testimony_ids = []
    for row in TESTIMONIES:
        t, _ = MannaTestimony.objects.update_or_create(
            title=row['title'], app_key=APP, language=LANG, platform='',
            defaults={
                'body': row.get('body') or '',
                'author_label': row.get('author_label') or '',
                'cover_url': '',
                'enabled': True,
                'sort_order': int(row.get('sort_order') or 0),
            },
        )
        testimony_ids.append(t.id)

    lv1 = level_ids.get(1)
    primary_testimony = testimony_ids[0] if testimony_ids else None
    cards = [
        ('daily_quiz', 'Daily Quiz', 'Grow with leveled questions',
         '/pages/quiz/quiz',
         {'level_id': lv1} if lv1 else {}),
        ('daily_devotion', 'Daily Devotion', 'Themed series for the journey',
         '/pagesA/devotion/detail' if series_id else '/pages/devotion/list',
         {'series_id': series_id, 'target_id': series_id} if series_id else {}),
        ('testimony', 'Testimony', 'Stories of grace',
         '/pages/profile/profile',
         {'testimony_id': primary_testimony} if primary_testimony else {}),
        ('story', 'Bible Stories', 'Unlock knowledge cards',
         '/pagesA/quiz/stories',
         {'story_id': story_ids[0]} if story_ids else {}),
    ]
    for i, (ctype, title, sub, link, payload) in enumerate(cards):
        MannaHomeCard.objects.update_or_create(
            card_type=ctype, title=title, app_key=APP, language=LANG, platform='',
            defaults={
                'subtitle': sub, 'cover_url': '', 'link_path': link,
                'payload': payload,
                'sort_order': i, 'enabled': True, 'active_date': None,
            },
        )

    for i, line in enumerate(STATS_LINES):
        MannaStatsCopy.objects.update_or_create(
            line=line, app_key=APP, language=LANG, platform='',
            defaults={'enabled': True, 'sort_order': i},
        )

    # Tag → 默认推荐系列 title（未学过时作为 featured；运营可改）
    tag_featured_title = {
        'anxiety': 'Overcoming Anxiety',
        'peace': 'Overcoming Anxiety',
        'love': 'Learning to Love',
        'family': 'Building the Family',
        'hope': 'Rekindling Hope',
        'healing': 'Rekindling Hope',
    }
    title_to_id = {
        s.title: s.id
        for s in DevotionSeries.objects.filter(app_key=APP, language=LANG, enabled=True)
    }
    if series_id and 'Overcoming Anxiety' not in title_to_id:
        title_to_id['Overcoming Anxiety'] = series_id

    for key, display, order in TAGS:
        featured_id = title_to_id.get(tag_featured_title.get(key) or '')
        MannaContentTag.objects.update_or_create(
            key=key, app_key=APP, language=LANG, platform='',
            defaults={
                'display_name': display,
                'enabled': True,
                'sort_order': order,
                'featured_series_id': featured_id,
            },
        )

    return {
        'testimonies': len(testimony_ids),
        'cards': len(cards),
        'stats': len(STATS_LINES),
        'tags': len(TAGS),
        'primary_testimony_id': primary_testimony,
    }


def seed_demo_activity():
    """给已有 Manna 钱包用户补本月若干活动日，避免日历全空不便联调。"""
    from models.models import ActivityDay, MannaWallet
    today = dj_tz.now().date()
    user_ids = list(
        MannaWallet.objects.filter(app_key=APP).values_list('user_id', flat=True)[:20]
    )
    if not user_ids:
        return 0
    n = 0
    offsets = (0, 1, 2, 4, 5, 8, 11)
    for uid in user_ids:
        for off in offsets:
            day = today - timedelta(days=off)
            _, created = ActivityDay.objects.update_or_create(
                user_id=uid, app_key=APP, date=day,
                defaults={
                    'flags': {'seed': True},
                    'pray_sec': 120 if off % 2 == 0 else 0,
                    'devotion_sec': 300 if off % 3 == 0 else 0,
                    'read_sec': 180 if off in (0, 1, 5) else 0,
                    'quiz_count': 1 if off in (0, 2, 8) else 0,
                },
            )
            if created:
                n += 1
    return n


def seed_scope_samples():
    """对照包已收口：不再另写 zh/ios 经文行（会多出一条今日经文）。"""
    PrayerContent.objects.filter(
        app_key=APP, language='zh',
        title__in=('晨祷·安静（zh/ios）', '晚祷·安息（zh/ios）'),
    ).delete()
    return 0


def seed_welcome_and_login_copy():
    """WelcomeScreen + 登录文案 AppConfig：按 app_key×language×platform 独立于 Selah。"""
    # 全语言/全设备默认
    WelcomeScreen.objects.update_or_create(
        title='Manna', app_key=APP, language='', platform='',
        defaults={
            'subtitle': 'Daily Scripture · Amen · Grow',
            'cta_text': 'Continue',
            'image_url': '/static/ui/home/bg-279-364.png',
            'version': 1,
            'sort_order': 0,
            'enabled': True,
        },
    )
    # 中文
    WelcomeScreen.objects.update_or_create(
        title='Manna', app_key=APP, language='zh', platform='',
        defaults={
            'subtitle': '每日经文 · Amen · 成长',
            'cta_text': '继续',
            'image_url': '/static/ui/home/bg-279-364.png',
            'version': 1,
            'sort_order': 0,
            'enabled': True,
        },
    )
    # iOS 对照（英文）
    WelcomeScreen.objects.update_or_create(
        title='Manna', app_key=APP, language='en', platform='ios',
        defaults={
            'subtitle': 'Daily Scripture · Amen · Grow',
            'cta_text': 'Continue',
            'image_url': '/static/ui/home/bg-279-364.png',
            'version': 1,
            'sort_order': 1,
            'enabled': True,
        },
    )

    login_copies = [
        ('', '', 'Sign in to Manna', 'Continue with Apple, Google, or a quick device login.'),
        ('zh', '', '登录 Manna', '使用 Apple、Google 或快速登录继续。'),
        ('en', 'ios', 'Sign in to Manna', 'Continue with Apple, Google, or a quick device login.'),
    ]
    for lang, plat, title, sub in login_copies:
        AppConfig.objects.update_or_create(
            key='login_title', app_key=APP, language=lang, platform=plat, priority=0,
            defaults={'value': title, 'remark': 'manna-seed'},
        )
        AppConfig.objects.update_or_create(
            key='login_subtitle', app_key=APP, language=lang, platform=plat, priority=0,
            defaults={'value': sub, 'remark': 'manna-seed'},
        )
    # 确保 welcome 开关开着（manna 维度）
    AppConfig.objects.update_or_create(
        key='welcome_enabled', app_key=APP, language='', platform='', priority=0,
        defaults={'value': True, 'remark': 'manna-seed'},
    )
    return 3 + len(login_copies) * 2


def main():
    ensure_manna_defaults()
    migrate_ops_lang_to_all()
    if '--scene-bgs' in sys.argv:
        n_scene = rewrite_home_pool_on_other_surfaces()
        sys.stderr.write(f'seed_manna scene-bgs rewrite={n_scene}\n')
        return
    if '--catalog' in sys.argv:
        series_id, n_series, n_sessions = seed_devotion()
        level_ids, n_questions, story_ids = seed_quiz()
        n_scene = rewrite_home_pool_on_other_surfaces()
        prune_orphans()
        sys.stderr.write(
            f'seed_manna catalog devotion={n_series}/{n_sessions} '
            f'quiz_levels={len(level_ids)} quiz_questions={n_questions} '
            f'stories={len(story_ids)} series_id={series_id} scene_rewrite={n_scene}\n'
        )
        return
    quiz_only = '--quiz-only' in sys.argv
    if quiz_only:
        level_ids, n_questions, story_ids = seed_quiz()
        prune_orphans()
        levels_s = ','.join(f'{k}:{v}' for k, v in sorted(level_ids.items()))
        sys.stderr.write(
            f'seed_manna quiz-only levels={len(level_ids)} questions={n_questions} '
            f'stories={len(story_ids)} levels={{{levels_s}}} story_ids={story_ids}\n'
        )
        return
    n_welcome = seed_welcome_and_login_copy()
    n_verses = seed_daily_verses()
    n_themes = seed_versecard_themes()
    if '--ops' in sys.argv:
        series_id, n_series, n_sessions = seed_devotion()
        n_prayers, n_badges = seed_prayer_badges()
        n_scope = seed_scope_samples()
        n_scene = rewrite_home_pool_on_other_surfaces()
        sys.stderr.write(
            f'seed_manna ops-only verses={n_verses} versecard_themes={n_themes} '
            f'devotion={n_series}/{n_sessions} '
            f'prayers={n_prayers} badges={n_badges} scope={n_scope} welcome={n_welcome} '
            f'scene_rewrite={n_scene}\n'
        )
        return
    core, full_stats, web_verses = seed_bible()
    series_id, n_series, n_sessions = seed_devotion()
    if '--covers' in sys.argv or os.getenv('MANNA_SEED_COVERS', '').strip() in ('1', 'true', 'yes'):
        try:
            from seed_manna_devotion_covers import seed_covers
            force = '--force-covers' in sys.argv
            u, s = seed_covers(force=force)
            sys.stderr.write(f'seed_manna_devotion_covers updated={u} skipped={s}\n')
        except Exception as e:
            sys.stderr.write(f'seed_manna_devotion_covers skipped: {e}\n')
    level_ids, n_questions, story_ids = seed_quiz()
    n_prayers, n_badges = seed_prayer_badges()
    home = seed_home_2_0(series_id, level_ids, story_ids)
    n_activity = seed_demo_activity()
    n_scope = seed_scope_samples()
    n_scene = rewrite_home_pool_on_other_surfaces()
    prune_orphans()
    levels_s = ','.join(f'{k}:{v}' for k, v in sorted(level_ids.items()))
    msg = (
        f'seed_manna done welcome_login_rows={n_welcome} daily_verses={n_verses} '
        f'versecard_themes={n_themes} '
        f'devotion_series={n_series} devotion_sessions={n_sessions} '
        f'quiz_levels={len(level_ids)} quiz_questions={n_questions} stories={len(story_ids)} '
        f'prayers={n_prayers} badges={n_badges} activity_days={n_activity} '
        f'scope_samples={n_scope} scene_rewrite={n_scene} '
        f'testimonies={home["testimonies"]} home_cards={home["cards"]} '
        f'stats={home["stats"]} tags={home["tags"]} '
        f'bible_core={core} bible_full={full_stats or None} web_seed_verses={web_verses} '
        f'series_id={series_id} levels={{{levels_s}}} '
        f'story_ids={story_ids} testimony_id={home["primary_testimony_id"]}\n'
    )
    sys.stderr.write(msg)


if __name__ == '__main__':
    main()
