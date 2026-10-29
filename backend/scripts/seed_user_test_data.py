#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
为指定用户灌入可测内容（喜欢/收藏/历史/关注/壁纸/打卡/肯定语/AI 会话）。

默认按「可滚动」体量灌入：关注话题 / 曾用壁纸 / 喜欢 / 历史等足够撑起列表滚动。
目录不够时会自动补 virtual 话题与壁纸。

用法（在 backend 目录）:
  ./uivsbe_env/bin/python scripts/seed_user_test_data.py --user-id 57 --create
  ./uivsbe_env/bin/python scripts/seed_user_test_data.py --user-id 57 --create --topics 48 --wallpapers 48
  ./uivsbe_env/bin/python scripts/seed_user_test_data.py --user-id 75
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import django

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from django.utils import timezone  # noqa: E402
from urllib.parse import quote  # noqa: E402

from models.models import (  # noqa: E402
    Affirmation,
    AiConversation,
    AiMessage,
    CheckinRecord,
    CollectionFolder,
    CollectionItem,
    Quote,
    Topic,
    TopicFollow,
    User,
    UserContentHistory,
    UserDislike,
    UserLike,
    Wallpaper,
    WallpaperUsage,
)

AFFIRMATIONS_BASE = [
    'I am grounded in peace today.',
    'Grace meets me in the quiet.',
    'I release what I cannot carry.',
    'My heart is open to hope.',
    'I choose stillness over hurry.',
    'I am held, even when unsure.',
    'Light finds me again tomorrow.',
    'Today I breathe and begin again.',
    'I walk gently with myself.',
    'Hope is rising in me.',
]

TOPIC_NAMES = [
    'Peace', 'Hope', 'Gratitude', 'Courage', 'Healing', 'Prayer', 'Faith', 'Joy',
    'Rest', 'Wisdom', 'Mercy', 'Trust', 'Love', 'Patience', 'Strength', 'Comfort',
    'Purpose', 'Family', 'Friendship', 'Forgiveness', 'Humility', 'Guidance', 'Worship',
    'Scripture', 'Morning', 'Evening', 'Sabbath', 'Lament', 'Praise', 'Stillness',
    'Renewal', 'Grace', 'Kindness', 'Truth', 'Freedom', 'Abundance', 'Contentment',
    'Endurance', 'Compassion', 'Clarity', 'Refuge', 'Promise', 'Light', 'Anchor',
    'Breath', 'Presence', 'Devotion', 'Wonder',
]

COLORS = ['B6BADF', 'FFF0D9', 'F5DFBC', 'C5C3E8', 'FBEED8', 'E8E6F5', 'D8E8E0', 'F0D8E0']


def _placeholder(w: int, h: int, text: str, bg: str = 'C5C3E8', fg: str = '191919') -> str:
    return (
        f'https://placehold.co/{w}x{h}/{bg}/{fg}/png'
        f'?text={quote(str(text)[:28])}&font=roboto'
    )


def ensure_scroll_catalog(min_topics: int, min_wallpapers: int, min_quotes: int) -> dict:
    """目录不够撑滚动时补 virtual 话题/壁纸/语录。"""
    created_topics = 0
    existing_names = set(Topic.objects.filter(is_deleted=False).values_list('name', flat=True))
    n_topics = Topic.objects.filter(is_deleted=False, status=1).count()
    i = 0
    while n_topics + created_topics < min_topics:
        name = TOPIC_NAMES[i] if i < len(TOPIC_NAMES) else f'Scroll Topic {i + 1}'
        i += 1
        if name in existing_names:
            continue
        Topic.objects.create(
            name=name,
            category='scroll_seed',
            description=f'Seed topic for scroll QA: {name}',
            cover_url=_placeholder(600, 600, name, bg=COLORS[created_topics % len(COLORS)]),
            is_vip=(created_topics % 6 == 0),
            sort_weight=200 - created_topics,
            status=1,
        )
        existing_names.add(name)
        created_topics += 1

    created_wp = 0
    n_wp = Wallpaper.objects.filter(is_deleted=False, status=1).count()
    while n_wp + created_wp < min_wallpapers:
        idx = n_wp + created_wp + 1
        Wallpaper.objects.create(
            image_url=_placeholder(
                750, 1334, f'Selah WP {idx}',
                bg=COLORS[created_wp % len(COLORS)],
            ),
            is_vip=(created_wp % 5 == 0),
            tags=['virtual', 'scroll_seed'],
            sort_weight=200 - created_wp,
            status=1,
        )
        created_wp += 1

    created_quotes = 0
    n_q = Quote.objects.filter(is_deleted=False, status=1).count()
    topics = list(Topic.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', 'id')[:20])
    while n_q + created_quotes < min_quotes:
        idx = n_q + created_quotes + 1
        topic = topics[created_quotes % len(topics)] if topics else None
        tag = ['peace', 'hope', 'prayer'][created_quotes % 3]
        Quote.objects.create(
            content=f'Scroll QA quote #{idx}: peace grows in quiet trust.',
            source_ref='Selah Seed',
            interest_tags=[tag],
            topic_id=topic.id if topic else None,
            in_cold_start_pool=(created_quotes % 4 == 0),
            sort_weight=100 - created_quotes,
            status=1,
            language='en',
        )
        created_quotes += 1

    return {
        'topics_created': created_topics,
        'wallpapers_created': created_wp,
        'quotes_created': created_quotes,
        'topics_total': Topic.objects.filter(is_deleted=False, status=1).count(),
        'wallpapers_total': Wallpaper.objects.filter(is_deleted=False, status=1).count(),
        'quotes_total': Quote.objects.filter(is_deleted=False, status=1).count(),
    }


def ensure_user(uid: int, create: bool) -> User:
    u = User.objects.filter(id=uid).first()
    if u:
        return u
    if not create:
        raise SystemExit(f'User id={uid} not found (pass --create to create)')
    # 指定 id 创建，便于联调固定账号
    u = User(
        id=uid,
        device_id=f'seed_scroll_user_{uid}',
        username=f'seed_user_{uid}',
        display_name=f'Scroll QA {uid}',
        login_type='device',
        role='user',
        status=1,
        onboarding_completed=True,
        free_quota=20,
        checkin_streak=12,
        streak_hearts=3,
        checkin_record_enabled=True,
    )
    u.save()
    return u


def seed_user(
    uid: int,
    *,
    create: bool = False,
    min_topics: int = 48,
    min_wallpapers: int = 48,
    min_quotes: int = 120,
    likes: int = 60,
    history: int = 80,
    follows: int | None = None,
    wallpapers_used: int | None = None,
    affirmations: int = 40,
    collect: int = 48,
) -> None:
    catalog = ensure_scroll_catalog(min_topics, min_wallpapers, min_quotes)
    print('Catalog:', catalog)

    u = ensure_user(uid, create=create)

    quotes = list(
        Quote.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', '-id')[:max(min_quotes, likes, history, collect)]
    )
    topics = list(Topic.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', '-id'))
    wallpapers = list(Wallpaper.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', '-id'))
    if not quotes:
        raise SystemExit('No quotes in catalog; seed content first')

    follow_n = follows if follows is not None else len(topics)
    used_n = wallpapers_used if wallpapers_used is not None else len(wallpapers)

    today = date.today()
    now = timezone.now()

    u.onboarding_completed = True
    u.onboarding_skipped = False
    u.faith_type = u.faith_type or 'learning'
    u.prayer_frequency = u.prayer_frequency or 'weekly'
    u.interest_tags = u.interest_tags or ['prayer', 'peace', 'hope', 'gratitude']
    u.gender = u.gender or 'prefer_not'
    u.age_range = u.age_range or '25-34'
    u.free_quota = max(u.free_quota or 0, 20)
    u.newcomer_bonus_granted = True
    u.newcomer_like_completed = True
    u.checkin_streak = max(u.checkin_streak or 0, 12)
    u.last_checkin_date = today - timedelta(days=1)
    u.streak_hearts = max(u.streak_hearts or 0, 3)
    u.checkin_record_enabled = True
    u.last_active_at = now
    if not (u.display_name or '').strip():
        u.display_name = f'Scroll QA {uid}'
    if wallpapers:
        u.wallpaper_id = wallpapers[0].id
    u.save()

    # ----- 喜欢 / 不喜欢 / 曝光历史 -----
    UserLike.objects.filter(user_id=uid).delete()
    for q in quotes[:likes]:
        UserLike.objects.get_or_create(user_id=uid, content_id=q.id)

    UserDislike.objects.filter(user_id=uid).delete()
    if len(quotes) > likes + 5:
        UserDislike.objects.get_or_create(user_id=uid, content_id=quotes[likes + 5].id)

    UserContentHistory.objects.filter(user_id=uid).delete()
    for q in quotes[:history]:
        UserContentHistory.objects.create(user_id=uid, content_id=q.id, action='expose')

    # ----- 收藏夹 -----
    default_folder, _ = CollectionFolder.objects.update_or_create(
        user_id=uid, name='Favorites', defaults={'is_default': True},
    )
    morning, _ = CollectionFolder.objects.update_or_create(
        user_id=uid, name='Morning Peace', defaults={'is_default': False},
    )
    evening, _ = CollectionFolder.objects.update_or_create(
        user_id=uid, name='Evening Rest', defaults={'is_default': False},
    )
    CollectionItem.objects.filter(user_id=uid).delete()
    third = max(1, collect // 3)
    for q in quotes[:third]:
        CollectionItem.objects.get_or_create(
            user_id=uid, content_id=q.id, defaults={'folder_id': default_folder.id},
        )
    for q in quotes[third: third * 2]:
        CollectionItem.objects.update_or_create(
            user_id=uid, content_id=q.id, defaults={'folder_id': morning.id},
        )
    for q in quotes[third * 2: collect]:
        CollectionItem.objects.update_or_create(
            user_id=uid, content_id=q.id, defaults={'folder_id': evening.id},
        )

    # ----- 关注主题（尽量全部，便于 followed 列表滚动）-----
    TopicFollow.objects.filter(user_id=uid).delete()
    for t in topics[:follow_n]:
        TopicFollow.objects.get_or_create(user_id=uid, topic_id=t.id)

    # ----- 曾用壁纸 -----
    WallpaperUsage.objects.filter(user_id=uid).delete()
    for w in wallpapers[:used_n]:
        WallpaperUsage.objects.get_or_create(user_id=uid, wallpaper_id=w.id)

    # ----- 打卡记录 -----
    CheckinRecord.objects.filter(user_id=uid).delete()
    for i in range(14, 0, -1):
        d = today - timedelta(days=i)
        status = CheckinRecord.STATUS_FROZEN if i == 4 else CheckinRecord.STATUS_CHECKED
        CheckinRecord.objects.create(
            user_id=uid,
            local_date=d,
            status=status,
            granted_quota=0 if status == CheckinRecord.STATUS_FROZEN else 1,
            hit_cap=False,
            vip_level=u.vip_level or 'free',
        )

    # ----- 自我肯定语 -----
    Affirmation.objects.filter(user_id=uid).delete()
    texts = list(AFFIRMATIONS_BASE)
    while len(texts) < affirmations:
        texts.append(f'I receive today’s mercy #{len(texts) + 1}.')
    for text in texts[:affirmations]:
        Affirmation.objects.create(user_id=uid, content=text)

    # ----- AI 会话 -----
    old_conv_ids = list(AiConversation.objects.filter(user_id=uid).values_list('id', flat=True))
    if old_conv_ids:
        AiMessage.objects.filter(conversation_id__in=old_conv_ids).delete()
    AiConversation.objects.filter(user_id=uid).delete()
    conv = AiConversation.objects.create(user_id=uid, local_date=today)
    pairs = [
        ('I feel anxious this morning.', 'Take a slow breath. You are not alone in this moment.'),
        ('Yes, please share a short prayer.', 'Lord, quiet my racing thoughts and fill this heart with your peace. Amen.'),
        ('Can you give me a verse about hope?', 'Hope anchors the soul. Hold onto the promise that light returns.'),
    ]
    for user_text, assistant_text in pairs:
        AiMessage.objects.create(conversation_id=conv.id, user_id=uid, role='user', content=user_text)
        AiMessage.objects.create(
            conversation_id=conv.id, user_id=uid, role='assistant', content=assistant_text, deducted=True,
        )

    print(
        f'Seeded user id={uid} ({u.display_name or u.username or u.device_id})\n'
        f'  likes={UserLike.objects.filter(user_id=uid).count()} '
        f'history={UserContentHistory.objects.filter(user_id=uid).count()} '
        f'collect={CollectionItem.objects.filter(user_id=uid).count()} '
        f'folders={CollectionFolder.objects.filter(user_id=uid).count()}\n'
        f'  follows={TopicFollow.objects.filter(user_id=uid).count()} '
        f'wallpapers_used={WallpaperUsage.objects.filter(user_id=uid).count()} '
        f'checkins={CheckinRecord.objects.filter(user_id=uid).count()} '
        f'affirmations={Affirmation.objects.filter(user_id=uid).count()} '
        f'ai_msgs={AiMessage.objects.filter(conversation_id=conv.id).count()}\n'
        f'  wallpaper_id={u.wallpaper_id} streak={u.checkin_streak} free_quota={u.free_quota}'
    )


def main():
    parser = argparse.ArgumentParser(description='Seed rich scrollable test data for one App user')
    parser.add_argument('--user-id', type=int, required=True)
    parser.add_argument('--create', action='store_true', help='Create user if missing (keeps given id)')
    parser.add_argument('--topics', type=int, default=48, help='Min catalog topics (also follow count)')
    parser.add_argument('--wallpapers', type=int, default=48, help='Min catalog wallpapers (also used count)')
    parser.add_argument('--quotes', type=int, default=120, help='Min catalog quotes')
    parser.add_argument('--likes', type=int, default=60)
    parser.add_argument('--history', type=int, default=80)
    parser.add_argument('--affirmations', type=int, default=40)
    parser.add_argument('--collect', type=int, default=48)
    args = parser.parse_args()
    seed_user(
        args.user_id,
        create=args.create,
        min_topics=args.topics,
        min_wallpapers=args.wallpapers,
        min_quotes=args.quotes,
        likes=args.likes,
        history=args.history,
        affirmations=args.affirmations,
        collect=args.collect,
    )


if __name__ == '__main__':
    main()
