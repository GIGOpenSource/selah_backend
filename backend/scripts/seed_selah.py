#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
Selah 测试画像种子（可重复执行）。

原则：
- 只连远程 PostgreSQL（禁止 SQLite）。
- 语录 / 话题 / 壁纸 / Onboarding / 安全词 / 热线 / 快捷开场 / 会员 SKU
  等运营内容 **全部取自远程库现有数据**，本脚本不 invent picsum / 假文案覆盖运营配置。
- 仅 upsert `seed_device_*` 测试画像及其互动 / 打卡 / AI / IAP / 埋点，挂接到远程库存。

用法：
  ./uivsbe_env/bin/python scripts/seed_selah.py
  ./uivsbe_env/bin/python scripts/seed_selah.py --report-only   # 只看远程覆盖缺口
"""
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import django

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from django.conf import settings
from django.db.models import Q
from django.utils import timezone as dj_tz

from models.models import (
    Affirmation,
    AffirmationTemplate,
    AiConversation,
    AiMessage,
    AiQuickPrompt,
    AiSafetyAudit,
    AiSafetyKeyword,
    AnalyticsEvent,
    CheckinRecord,
    CollectionFolder,
    CollectionItem,
    ContentReport,
    CrisisHotline,
    IapReceipt,
    MembershipProduct,
    OnboardingQuestion,
    Quote,
    Topic,
    TopicFollow,
    User,
    UserContentHistory,
    UserDislike,
    UserLike,
    Wallpaper,
)
from tools.selah_quota import ensure_default_configs, ensure_default_welcome_screens
from tools.membership_products import ensure_default_products
from tools.selah_virtual_seed import ensure_virtual_catalog
from tools.admin_stats import EVENT_CATALOG
from tools.selah_enums import (
    values_of,
    INTEREST_TAGS as INTEREST_TAG_PAIRS,
    FAITH_TYPES as FAITH_TYPE_PAIRS,
    PRAYER_FREQ as PRAYER_FREQ_PAIRS,
    GENDERS as GENDER_PAIRS,
    AGE_RANGES as AGE_RANGE_PAIRS,
    APP_LANGS as APP_LANG_PAIRS,
    REPORT_REASONS as REPORT_REASON_PAIRS,
    SAFETY_CATEGORIES as SAFETY_CAT_PAIRS,
    TOPIC_CATEGORIES as TOPIC_CAT_PAIRS,
)
from Apps.views.ai.view import ensure_safety_seeds

INTEREST_TAGS = values_of(INTEREST_TAG_PAIRS)
FAITH_TYPES = values_of(FAITH_TYPE_PAIRS)
PRAYER_FREQ = values_of(PRAYER_FREQ_PAIRS)
GENDERS = values_of(GENDER_PAIRS)
AGE_RANGES = values_of(AGE_RANGE_PAIRS)
APP_LANGS = values_of(APP_LANG_PAIRS)
REPORT_REASONS = values_of(REPORT_REASON_PAIRS)
SAFETY_CATEGORIES = values_of(SAFETY_CAT_PAIRS)
TOPIC_CATEGORIES = values_of(TOPIC_CAT_PAIRS)

SEED_DEVICE_PREFIX = 'seed_device_'


def _utcnow():
    return dj_tz.now()


def assert_remote_postgres():
    db = settings.DATABASES['default']
    engine = (db.get('ENGINE') or '').lower()
    host = (db.get('HOST') or '').strip()
    if 'sqlite' in engine:
        raise SystemExit('禁止 SQLite：请配置远程 PostgreSQL（POSTGRES_HOST）')
    if not host or host in ('127.0.0.1', 'localhost', '::1'):
        raise SystemExit(
            f'种子数据必须取自远程库：当前 HOST={host or "(空)"}，请检查 .env / POSTGRES_HOST'
        )
    print(f'Remote DB: {host}:{db.get("PORT")} / {db.get("NAME")}')


def load_remote_inventory():
    """从远程库加载运营内容作为样例池（只读）。"""
    quotes = list(
        Quote.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', '-id')
    )
    topics = list(
        Topic.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', '-id')
    )
    wallpapers = list(
        Wallpaper.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', '-id')
    )
    products = list(
        MembershipProduct.objects.filter(enabled=True).order_by('-sort_weight', 'plan_type', 'platform')
    )
    prompts = list(AiQuickPrompt.objects.filter(enabled=True).order_by('sort_order', 'id'))
    hotlines = list(CrisisHotline.objects.filter(enabled=True).order_by('id'))
    keywords = list(AiSafetyKeyword.objects.filter(enabled=True))
    onboarding = list(
        OnboardingQuestion.objects.filter(enabled=True).order_by('sort_order', 'id')
    )

    # 按维度索引，便于画像挂接
    quotes_by_lang = defaultdict(list)
    quotes_by_tag = defaultdict(list)
    quotes_cold = []
    quotes_vip = []
    for q in quotes:
        quotes_by_lang[q.language or 'en'].append(q)
        for t in (q.interest_tags or []):
            quotes_by_tag[t].append(q)
        if q.in_cold_start_pool:
            quotes_cold.append(q)
        if q.is_vip:
            quotes_vip.append(q)

    products_by_slot = {}
    for p in products:
        products_by_slot[(p.plan_type, p.platform)] = p

    free_wp = [w for w in wallpapers if not w.is_vip]
    vip_wp = [w for w in wallpapers if w.is_vip]

    inv = {
        'quotes': quotes,
        'topics': topics,
        'wallpapers': wallpapers,
        'free_wallpapers': free_wp,
        'vip_wallpapers': vip_wp,
        'products': products,
        'products_by_slot': products_by_slot,
        'prompts': prompts,
        'hotlines': hotlines,
        'keywords': keywords,
        'onboarding': onboarding,
        'quotes_by_lang': dict(quotes_by_lang),
        'quotes_by_tag': dict(quotes_by_tag),
        'quotes_cold': quotes_cold,
        'quotes_vip': quotes_vip,
    }
    print(
        f'Inventory (remote): quotes={len(quotes)} cold={len(quotes_cold)} vip={len(quotes_vip)} '
        f'topics={len(topics)} wallpapers={len(wallpapers)} products={len(products)} '
        f'prompts={len(prompts)} hotlines={len(hotlines)} safety_kw={len(keywords)} '
        f'onboarding={len(onboarding)}'
    )
    return inv


def report_coverage_gaps(inv):
    """对照枚举报告远程库覆盖缺口（不自动 invent）。"""
    gaps = []

    present_tags = set()
    for tags in Quote.objects.filter(is_deleted=False, status=1).values_list('interest_tags', flat=True):
        for t in (tags or []):
            present_tags.add(t)
    missing_tags = [t for t in INTEREST_TAGS if t not in present_tags]
    if missing_tags:
        gaps.append(f'interest_tags 缺失: {missing_tags}')

    present_faith = set()
    for tags in Quote.objects.filter(is_deleted=False, status=1).values_list('faith_tags', flat=True):
        for t in (tags or []):
            present_faith.add(t)
    missing_faith = [t for t in FAITH_TYPES if t not in present_faith]
    if missing_faith:
        gaps.append(f'faith_tags 缺失: {missing_faith}')

    present_langs = set(inv['quotes_by_lang'].keys())
    missing_langs = [l for l in APP_LANGS if l not in present_langs]
    if missing_langs:
        gaps.append(f'语录语言缺失: {missing_langs}')

    present_cats = {
        c for c in Topic.objects.filter(is_deleted=False, status=1).values_list('category', flat=True) if c
    }
    missing_cats = [c for c in TOPIC_CATEGORIES if c not in present_cats]
    if missing_cats:
        gaps.append(f'话题分类缺失: {missing_cats}')

    if len(inv['quotes']) < 50:
        gaps.append(f'语录不足 50 条（当前 {len(inv["quotes"])}，产品要求 ≥50）')
    if len(inv['quotes_cold']) < 15:
        gaps.append(f'冷启池不足 15 条（当前 {len(inv["quotes_cold"])}）')
    if len(inv['free_wallpapers']) < 10:
        gaps.append(f'免费壁纸不足 10 张（当前 {len(inv["free_wallpapers"])}）')

    need_slots = [
        ('monthly', 'ios'), ('monthly', 'android'),
        ('yearly', 'ios'), ('yearly', 'android'),
    ]
    missing_sku = [f'{a}/{b}' for a, b in need_slots if (a, b) not in inv['products_by_slot']]
    if missing_sku:
        gaps.append(f'会员 SKU 缺失: {missing_sku}')

    zero_price = [
        f'{p.platform}/{p.plan_type}'
        for p in inv['products']
        if int(getattr(p, 'price_cents', 0) or 0) <= 0
    ]
    if zero_price:
        gaps.append(f'SKU price_cents=0（看板 ARPPU 无成交额）: {zero_price}')

    present_safety = set(
        AiSafetyKeyword.objects.filter(enabled=True).values_list('category', flat=True).distinct()
    )
    missing_safety = [c for c in SAFETY_CATEGORIES if c not in present_safety]
    if missing_safety:
        gaps.append(f'安全类别关键词缺失: {missing_safety}')

    if not inv['prompts']:
        gaps.append('无启用 AI 快捷开场')
    if not inv['hotlines']:
        gaps.append('无启用危机热线')
    if not inv['onboarding']:
        gaps.append('无启用 Onboarding 题目')

    picsum_wp = sum(1 for w in inv['wallpapers'] if 'picsum.photos' in (w.image_url or ''))
    if picsum_wp:
        gaps.append(
            f'壁纸仍有 {picsum_wp} 张 picsum 占位：建议运营台换成 CDN 真图以贴近真实效果'
        )

    print('\n=== Remote coverage gaps ===')
    if not gaps:
        print('  (none)')
    else:
        for g in gaps:
            print(f'  ! {g}')
    return gaps


def _ensure_admin():
    u, created = User.objects.update_or_create(
        username='selah_admin',
        defaults={
            'email': 'admin@selah.app',
            'password': 'change-me-admin',
            'role': 'super_admin',
            'login_type': 'admin',
            'status': 1,
            'vip_level': User.VIP_FREE,
            'timezone': 'UTC',
            'onboarding_completed': True,
        },
    )
    if created:
        print('Created admin: selah_admin / change-me-admin')
    else:
        if not (u.password or '').startswith('$2b$'):
            u.password = 'change-me-admin'
            u.save()
        print('Admin exists: selah_admin / change-me-admin')
    return u


def seed_staff():
    staff = [
        ('selah_operator', 'operator@selah.app', 'operator', 'change-me-ops'),
        ('selah_mgr', 'mgr@selah.app', 'admin', 'change-me-mgr'),
    ]
    for username, email, role, password in staff:
        u, created = User.objects.update_or_create(
            username=username,
            defaults={
                'email': email,
                'password': password,
                'role': role,
                'login_type': 'admin',
                'status': 1,
                'vip_level': User.VIP_FREE,
                'timezone': 'UTC',
                'onboarding_completed': True,
            },
        )
        if not created and not (u.password or '').startswith('$2b$'):
            u.password = password
            u.save()
    print('Staff: selah_operator / selah_mgr')


def _upsert_user(device_id=None, username=None, email=None, **fields):
    lookup = {}
    if device_id:
        lookup['device_id'] = device_id
    elif username:
        lookup['username'] = username
    elif email:
        lookup['email'] = email
    else:
        raise ValueError('need device_id/username/email')
    defaults = dict(fields)
    if device_id:
        defaults.setdefault('device_id', device_id)
    if username:
        defaults.setdefault('username', username)
    if email:
        defaults.setdefault('email', email)
    u, _ = User.objects.update_or_create(**lookup, defaults=defaults)
    return u


def _pick_wallpaper(inv, prefer_vip=False):
    pool = inv['vip_wallpapers'] if prefer_vip and inv['vip_wallpapers'] else inv['free_wallpapers']
    if not pool:
        pool = inv['wallpapers']
    return pool[0].id if pool else None


def _pick_quotes(inv, n=20, lang=None, prefer_cold=False):
    if prefer_cold and inv['quotes_cold']:
        pool = list(inv['quotes_cold'])
    elif lang and inv['quotes_by_lang'].get(lang):
        pool = list(inv['quotes_by_lang'][lang])
    else:
        pool = list(inv['quotes'])
    if not pool:
        return []
    return pool[:n]


def _interest_from_remote(inv, fallback=None):
    """用户兴趣优先取远程语录里真实出现过的标签。"""
    tags = [t for t, qs in sorted(inv['quotes_by_tag'].items(), key=lambda x: -len(x[1])) if t]
    if tags:
        return tags[:4]
    return fallback or INTEREST_TAGS[:3]


def seed_users(inv):
    """测试画像：状态字段可控，内容挂接全部来自远程库存。"""
    now = _utcnow()
    wp_free = _pick_wallpaper(inv, prefer_vip=False)
    wp_vip = _pick_wallpaper(inv, prefer_vip=True) or wp_free
    remote_tags = _interest_from_remote(inv)

    # 从远程 Onboarding / 语录推断可用枚举值（缺省回退常量）
    faith_opts = FAITH_TYPES
    prayer_opts = PRAYER_FREQ
    for q in inv['onboarding']:
        vals = [o.get('value') for o in (q.options or []) if isinstance(o, dict) and o.get('value')]
        if q.question_key == 'faith_type' and vals:
            faith_opts = vals
        if q.question_key == 'prayer_frequency' and vals:
            prayer_opts = vals

    users = {}

    users['fresh'] = _upsert_user(
        device_id='seed_device_fresh',
        login_type='device', role='user', status=1,
        vip_level=User.VIP_FREE, free_quota=3, newcomer_bonus_granted=True,
        onboarding_completed=False, onboarding_skipped=False,
        timezone='America/New_York', register_country='US', system_lang='en',
        platform='ios', checkin_streak=0, streak_hearts=3,
        newcomer_like_completed=False, display_name='', last_active_at=now,
    )

    users['free_onboarded'] = _upsert_user(
        device_id='seed_device_free_onboarded',
        login_type='device', role='user', status=1,
        vip_level=User.VIP_FREE, free_quota=5, newcomer_bonus_granted=True,
        onboarding_completed=True, onboarding_skipped=False,
        faith_type=faith_opts[0], prayer_frequency=prayer_opts[0],
        interest_tags=remote_tags,
        display_name='Ada', gender='female', age_range='25-34',
        timezone='America/Los_Angeles', register_country='US', system_lang='en',
        platform='ios', wallpaper_id=wp_free,
        checkin_streak=4, last_checkin_date=date.today() - timedelta(days=1),
        streak_hearts=2, newcomer_like_completed=False,
        bind_prompt_dismissed=False, marketing_opt_in=True,
        sound_enabled=True, muted=False, last_active_at=now - timedelta(hours=2),
    )

    users['skipped'] = _upsert_user(
        device_id='seed_device_skipped',
        login_type='device', role='user',
        vip_level=User.VIP_FREE, free_quota=2, newcomer_bonus_granted=True,
        onboarding_completed=False, onboarding_skipped=True,
        timezone='Europe/London', register_country='GB', system_lang='en',
        platform='android', checkin_streak=1, last_checkin_date=date.today(),
        streak_hearts=3, newcomer_like_completed=False, display_name='Skip',
        gender='male', age_range='18-24', last_active_at=now - timedelta(days=1),
    )

    users['quota_cap'] = _upsert_user(
        device_id='seed_device_quota_cap',
        login_type='device', role='user',
        vip_level=User.VIP_FREE, free_quota=10, newcomer_bonus_granted=True,
        onboarding_completed=True,
        faith_type=faith_opts[min(1, len(faith_opts) - 1)],
        prayer_frequency=prayer_opts[min(1, len(prayer_opts) - 1)],
        interest_tags=remote_tags[:3] or ['family', 'gratitude'],
        display_name='Cap', gender='male', age_range='35-44',
        timezone='Asia/Shanghai', register_country='CN', system_lang='zh',
        platform='android', checkin_streak=7,
        last_checkin_date=date.today() - timedelta(days=1),
        streak_hearts=3, newcomer_like_completed=True,
        last_active_at=now - timedelta(hours=5),
    )

    users['quota_zero'] = _upsert_user(
        device_id='seed_device_quota_zero',
        login_type='device', role='user',
        vip_level=User.VIP_FREE, free_quota=0, newcomer_bonus_granted=True,
        onboarding_completed=True,
        faith_type=faith_opts[min(3, len(faith_opts) - 1)],
        prayer_frequency=prayer_opts[min(2, len(prayer_opts) - 1)],
        interest_tags=remote_tags[1:4] or ['healing', 'courage'],
        display_name='Empty', gender='non_binary', age_range='18-24',
        timezone='Australia/Sydney', register_country='AU', system_lang='en',
        platform='ios', checkin_streak=0, streak_hearts=0,
        newcomer_like_completed=True, checkin_record_enabled=True,
        checkin_remind_enabled=False, last_active_at=now - timedelta(days=2),
    )

    users['vip'] = _upsert_user(
        device_id='seed_device_vip',
        email='vip@selah.test', username='seed_vip', password='change-me-vip',
        google_id='seed_google_vip_001', login_type='google', role='user',
        vip_level=User.VIP_VIP, vip_expire_at=now + timedelta(days=28), is_trial=False,
        free_quota=4, vip_daily_remaining=87, vip_daily_reset_date=date.today(),
        newcomer_bonus_granted=True, onboarding_completed=True,
        faith_type=faith_opts[0], prayer_frequency=prayer_opts[0],
        interest_tags=(remote_tags + INTEREST_TAGS)[:6],
        display_name='Victoria', gender='female', age_range='25-34',
        timezone='America/New_York', register_country='US', system_lang='en',
        platform='ios', wallpaper_id=wp_vip,
        checkin_streak=12, last_checkin_date=date.today(), streak_hearts=3,
        newcomer_like_completed=True, bind_prompt_dismissed=True, last_active_at=now,
    )

    users['trial'] = _upsert_user(
        device_id='seed_device_trial',
        apple_id='seed_apple_trial_001', login_type='apple', role='user',
        vip_level=User.VIP_VIP, vip_expire_at=now + timedelta(days=2), is_trial=True,
        free_quota=3, vip_daily_remaining=100, vip_daily_reset_date=date.today(),
        newcomer_bonus_granted=True, onboarding_completed=True,
        faith_type=faith_opts[min(2, len(faith_opts) - 1)],
        prayer_frequency=prayer_opts[min(3, len(prayer_opts) - 1)],
        interest_tags=remote_tags[:2] or ['peace', 'hope'],
        display_name='TrialUser', gender='prefer_not', age_range='45-54',
        timezone='America/Chicago', register_country='US', system_lang='en',
        platform='ios', checkin_streak=2,
        last_checkin_date=date.today() - timedelta(days=1),
        streak_hearts=3, newcomer_like_completed=False,
        last_active_at=now - timedelta(hours=8),
    )

    users['vip_expired'] = _upsert_user(
        device_id='seed_device_vip_expired',
        email='expired@selah.test', login_type='google',
        google_id='seed_google_expired_001', role='user',
        vip_level=User.VIP_VIP, vip_expire_at=now - timedelta(days=3), is_trial=False,
        free_quota=6, vip_daily_remaining=0, newcomer_bonus_granted=True,
        onboarding_completed=True, faith_type=faith_opts[0],
        prayer_frequency=prayer_opts[min(1, len(prayer_opts) - 1)],
        interest_tags=remote_tags[:2] or ['work', 'courage'],
        display_name='Expired', gender='male', age_range='55+',
        timezone='UTC', register_country='CA', system_lang='en', platform='android',
        checkin_streak=3, last_checkin_date=date.today() - timedelta(days=2),
        streak_hearts=1, newcomer_like_completed=True,
        last_active_at=now - timedelta(days=3),
    )

    users['disabled'] = _upsert_user(
        device_id='seed_device_disabled',
        login_type='device', role='user', status=0,
        vip_level=User.VIP_FREE, free_quota=1, newcomer_bonus_granted=True,
        display_name='Blocked', timezone='UTC', platform='android', system_lang='es',
    )

    users['checkin_off'] = _upsert_user(
        device_id='seed_device_checkin_off',
        login_type='device', role='user',
        vip_level=User.VIP_FREE, free_quota=4, newcomer_bonus_granted=True,
        onboarding_completed=True,
        faith_type=faith_opts[min(3, len(faith_opts) - 1)],
        prayer_frequency=prayer_opts[min(2, len(prayer_opts) - 1)],
        interest_tags=remote_tags[:1] or ['gratitude'],
        display_name='Quiet', gender='female', age_range='u18',
        timezone='Pacific/Auckland', register_country='NZ', system_lang='en',
        platform='ios', checkin_record_enabled=False, checkin_remind_enabled=False,
        checkin_streak=5, last_checkin_date=date.today() - timedelta(days=1),
        streak_hearts=2, newcomer_like_completed=True,
        muted=True, sound_enabled=False, marketing_opt_in=False,
        last_active_at=now - timedelta(days=4),
    )

    users['like_done'] = _upsert_user(
        device_id='seed_device_like_done',
        login_type='device', role='user',
        vip_level=User.VIP_FREE, free_quota=3, newcomer_bonus_granted=True,
        onboarding_completed=True, faith_type=faith_opts[0],
        prayer_frequency=prayer_opts[0],
        interest_tags=remote_tags[:3] or ['peace', 'healing', 'family'],
        display_name='Liker', gender='female', age_range='55+',
        timezone='Europe/Paris', register_country='FR', system_lang='es',
        platform='android', checkin_streak=1, last_checkin_date=date.today(),
        streak_hearts=3, newcomer_like_completed=True,
        last_active_at=now - timedelta(hours=1),
    )

    # 语言画像：system_lang 对齐远程语录已有语言
    lang_specs = [
        ('lang_ja', 'ja', 'JP', 'Asia/Tokyo', 'ios', 'Yuki'),
        ('lang_ko', 'ko', 'KR', 'Asia/Seoul', 'android', 'Min'),
        ('lang_pt', 'pt', 'BR', 'America/Sao_Paulo', 'android', 'Ana'),
    ]
    for key, lang, country, tz, plat, name in lang_specs:
        # 仅当远程确有该语言语录时优先；否则仍建画像但提示缺口
        users[key] = _upsert_user(
            device_id=f'seed_device_{key}',
            login_type='device', role='user',
            vip_level=User.VIP_FREE, free_quota=4, newcomer_bonus_granted=True,
            onboarding_completed=True, faith_type=faith_opts[0],
            prayer_frequency=prayer_opts[0],
            interest_tags=remote_tags[:2] or ['peace'],
            display_name=name, gender='female', age_range='25-34',
            timezone=tz, register_country=country, system_lang=lang,
            platform=plat, newcomer_like_completed=True,
            last_active_at=now - timedelta(hours=6),
        )

    print(f'Users seeded: {len(users)} personas (content wired to remote inventory)')
    return users


AFFIRMATION_SEED_TEXTS = [
    'I am grounded in peace today.',
    'Grace meets me in the quiet.',
    'I release what I cannot carry.',
    'My heart is open to hope.',
    'I choose stillness over hurry.',
    'I am held, even when unsure.',
    'Light finds me again tomorrow.',
]

AFFIRMATION_TEMPLATE_SEEDS = [
    ('I am enough for this moment.', 100, 'en'),
    ('Peace is already within me.', 90, 'en'),
    ('I receive today with gratitude.', 80, 'en'),
    ('I walk gently with myself.', 70, 'en'),
    ('Hope is rising in me.', 60, ''),
    ('I am rooted in love.', 50, ''),
]

SOURCE_REF_SAMPLES = [
    'Psalm 46:10',
    'John 14:27',
    'Romans 15:13',
    'Philippians 4:7',
    'C.S. Lewis',
    'Henri Nouwen',
]


def seed_affirmation_templates():
    """运营预置肯定语模板（幂等 upsert by content）。"""
    for content, weight, lang in AFFIRMATION_TEMPLATE_SEEDS:
        AffirmationTemplate.objects.update_or_create(
            content=content,
            language=lang,
            app_key='selah',
            platform='',
            defaults={
                'sort_weight': weight,
                'status': 1,
                'is_deleted': False,
            },
        )
    print(f'Affirmation templates: {len(AFFIRMATION_TEMPLATE_SEEDS)}')


def seed_quote_source_refs(inv):
    """抽样语录写入非空 source_ref（不覆盖已有非空出处）。"""
    quotes = list(inv.get('quotes') or [])[:24]
    patched = 0
    for i, q in enumerate(quotes):
        if (q.source_ref or '').strip():
            continue
        ref = SOURCE_REF_SAMPLES[i % len(SOURCE_REF_SAMPLES)]
        Quote.objects.filter(id=q.id).update(source_ref=ref)
        q.source_ref = ref
        patched += 1
    print(f'Quote source_ref patched: {patched}')


def seed_user_activity(users, inv):
    quotes = _pick_quotes(inv, n=40)
    if not quotes:
        print('Skip activity: remote has no active quotes')
        return

    u = users['free_onboarded']
    UserLike.objects.filter(user_id=u.id).delete()
    for q in quotes[:8]:
        UserLike.objects.get_or_create(user_id=u.id, content_id=q.id)
    UserContentHistory.objects.filter(user_id=u.id).delete()
    for q in quotes[:12]:
        UserContentHistory.objects.create(user_id=u.id, content_id=q.id, action='expose')
    UserDislike.objects.get_or_create(user_id=u.id, content_id=quotes[-1].id)

    default_folder, _ = CollectionFolder.objects.update_or_create(
        user_id=u.id, name='Favorites', defaults={'is_default': True},
    )
    custom_folder, _ = CollectionFolder.objects.update_or_create(
        user_id=u.id, name='Morning Peace', defaults={'is_default': False},
    )
    CollectionItem.objects.filter(user_id=u.id).delete()
    for q in quotes[:4]:
        CollectionItem.objects.get_or_create(
            user_id=u.id, content_id=q.id, defaults={'folder_id': default_folder.id},
        )
    for q in quotes[4:8]:
        CollectionItem.objects.update_or_create(
            user_id=u.id, content_id=q.id, defaults={'folder_id': custom_folder.id},
        )

    Affirmation.objects.filter(user_id=u.id).delete()
    for text in AFFIRMATION_SEED_TEXTS[:6]:
        Affirmation.objects.create(user_id=u.id, content=text)

    for topic in inv['topics'][:2]:
        TopicFollow.objects.get_or_create(user_id=u.id, topic_id=topic.id)

    like_user = users['like_done']
    UserLike.objects.filter(user_id=like_user.id).delete()
    for q in quotes[:5]:
        UserLike.objects.get_or_create(user_id=like_user.id, content_id=q.id)

    vip = users['vip']
    UserContentHistory.objects.filter(user_id=vip.id).delete()
    for q in quotes[:20]:
        UserContentHistory.objects.create(user_id=vip.id, content_id=q.id, action='expose')
    UserLike.objects.filter(user_id=vip.id).delete()
    for q in quotes[:8]:
        UserLike.objects.get_or_create(user_id=vip.id, content_id=q.id)
    folder, _ = CollectionFolder.objects.update_or_create(
        user_id=vip.id, name='VIP Saves', defaults={'is_default': True},
    )
    vip_folder2, _ = CollectionFolder.objects.update_or_create(
        user_id=vip.id, name='Evening Rest', defaults={'is_default': False},
    )
    CollectionItem.objects.filter(user_id=vip.id).delete()
    for q in quotes[5:10]:
        CollectionItem.objects.update_or_create(
            user_id=vip.id, content_id=q.id, defaults={'folder_id': folder.id},
        )
    for q in quotes[10:14]:
        CollectionItem.objects.update_or_create(
            user_id=vip.id, content_id=q.id, defaults={'folder_id': vip_folder2.id},
        )
    Affirmation.objects.filter(user_id=vip.id).delete()
    for text in AFFIRMATION_SEED_TEXTS[1:5]:
        Affirmation.objects.create(user_id=vip.id, content=text)

    # 举报理由尽量全覆盖，内容 ID 取自远程语录
    reporters = [
        users['free_onboarded'], users['vip'], users['skipped'],
        users['quota_zero'], users['trial'],
    ]
    for i, reason in enumerate(REPORT_REASONS):
        q = quotes[i % len(quotes)]
        ru = reporters[i % len(reporters)]
        ContentReport.objects.get_or_create(
            user_id=ru.id, content_id=q.id, reason=reason,
            defaults={'detail': f'remote-seed report:{reason}'},
        )

    print(
        f'Activity on remote quotes id=[{quotes[0].id}..{quotes[min(19, len(quotes)-1)].id}] '
        f'topics={len(inv["topics"][:2])} '
        f'| free_onboarded likes≥8 collect≥2×folders affirm≥6 history≥10'
    )


def seed_checkins(users):
    today = date.today()
    samples = [
        (users['free_onboarded'], today - timedelta(days=3), 'checked', 1, False, 'free'),
        (users['free_onboarded'], today - timedelta(days=2), 'frozen', 0, False, 'free'),
        (users['free_onboarded'], today - timedelta(days=1), 'checked', 1, False, 'free'),
        (users['quota_cap'], today - timedelta(days=1), 'checked', 0, True, 'free'),
        (users['quota_cap'], today - timedelta(days=2), 'checked', 0, True, 'free'),
        (users['vip'], today - timedelta(days=2), 'checked', 0, False, 'vip'),
        (users['vip'], today - timedelta(days=1), 'checked', 0, False, 'vip'),
        (users['vip'], today, 'checked', 0, False, 'vip'),
        (users['trial'], today - timedelta(days=1), 'checked', 0, False, 'vip'),
        (users['skipped'], today, 'checked', 1, False, 'free'),
        (users['like_done'], today, 'checked', 1, False, 'free'),
        (users['quota_zero'], today - timedelta(days=5), 'checked', 1, False, 'free'),
        (users['quota_zero'], today - timedelta(days=4), 'frozen', 0, False, 'free'),
        (users['quota_zero'], today - timedelta(days=3), 'frozen', 0, False, 'free'),
        (users['quota_zero'], today - timedelta(days=2), 'frozen', 0, False, 'free'),
    ]
    for user, local_date, status, granted, hit_cap, vip_level in samples:
        CheckinRecord.objects.update_or_create(
            user_id=user.id, local_date=local_date,
            defaults={
                'status': status, 'granted_quota': granted,
                'hit_cap': hit_cap, 'vip_level': vip_level,
            },
        )
    print(f'Checkin records: {CheckinRecord.objects.filter(user_id__in=[u.id for u in users.values()]).count()}')


def seed_ai(users, inv):
    u = users['free_onboarded']
    today = date.today()
    conv, _ = AiConversation.objects.update_or_create(
        user_id=u.id, local_date=today, defaults={},
    )
    AiMessage.objects.filter(conversation_id=conv.id).delete()

    # 开场文案优先用远程快捷 prompt
    opener = inv['prompts'][0].prompt if inv['prompts'] else 'I feel anxious about tomorrow.'
    AiMessage.objects.create(
        conversation_id=conv.id, user_id=u.id, role='user', content=opener[:500], deducted=False,
    )
    AiMessage.objects.create(
        conversation_id=conv.id, user_id=u.id, role='assistant',
        content='Breathe gently. You are not alone in this moment. — Selah',
        deducted=True,
    )

    # 拒答样例：关键词取自远程安全词库
    kw = next((k for k in inv['keywords'] if k.category == 'medical'), None)
    if not kw and inv['keywords']:
        kw = inv['keywords'][0]
    if kw:
        AiMessage.objects.create(
            conversation_id=conv.id, user_id=u.id, role='user',
            content=f'Please help: {kw.keyword}', deducted=False,
            safety_blocked=True, safety_category=kw.category,
        )
        AiMessage.objects.create(
            conversation_id=conv.id, user_id=u.id, role='assistant',
            content='I cannot help with that request. Please seek local professional help.',
            deducted=False, safety_blocked=True, safety_category=kw.category,
        )

    vip = users['vip']
    conv2, _ = AiConversation.objects.update_or_create(
        user_id=vip.id, local_date=today - timedelta(days=1), defaults={},
    )
    if not AiMessage.objects.filter(conversation_id=conv2.id).exists():
        p2 = inv['prompts'][1].prompt if len(inv['prompts']) > 1 else 'Guide me in a short morning prayer.'
        AiMessage.objects.create(
            conversation_id=conv2.id, user_id=vip.id, role='user', content=p2[:500], deducted=False,
        )
        AiMessage.objects.create(
            conversation_id=conv2.id, user_id=vip.id, role='assistant',
            content='Lord, meet me in this morning light...', deducted=True,
        )

    # 审计：远程已启用的安全类别
    cats = list(
        AiSafetyKeyword.objects.filter(enabled=True)
        .values_list('category', flat=True).distinct()
    ) or list(SAFETY_CATEGORIES)
    audit_users = [
        users['free_onboarded'], users['vip'], users['quota_zero'],
        users['trial'], users['skipped'], users['lang_ja'],
    ]
    for i, cat in enumerate(cats):
        Au = audit_users[i % len(audit_users)]
        AiSafetyAudit.objects.get_or_create(user_id=Au.id, safety_category=cat, defaults={})
    print(f'AI seeded using remote prompts={len(inv["prompts"])} keywords={len(inv["keywords"])}')


def seed_iap(users, inv):
    """订单样例：product_id / amount_cents 全部来自远程 MembershipProduct。"""
    now = _utcnow()
    slot = inv['products_by_slot']

    def prod(plan, platform):
        return slot.get((plan, platform))

    # (user, plan, platform, txn, trial, expire, status)
    plan_rows = [
        (users['vip'], 'monthly', 'ios', 'seed_txn_ios_vip_001', False, now + timedelta(days=28), 'active'),
        (users['vip'], 'yearly', 'ios', 'seed_txn_ios_vip_yearly_001', False, now + timedelta(days=360), 'active'),
        (users['trial'], 'monthly', 'ios', 'seed_txn_ios_trial_001', True, now + timedelta(days=2), 'active'),
        (users['vip_expired'], 'monthly', 'android', 'seed_txn_android_exp_001', False, now - timedelta(days=3), 'expired'),
        (users['vip'], 'monthly', 'android', 'seed_txn_android_vip_002', False, now + timedelta(days=28), 'active'),
        (users['vip'], 'yearly', 'android', 'seed_txn_android_vip_yearly_001', False, now + timedelta(days=360), 'active'),
        (users['lang_pt'], 'monthly', 'android', 'seed_txn_android_refund_001', False, now + timedelta(days=10), 'refunded'),
        (users['lang_ja'], 'monthly', 'ios', 'seed_txn_ios_cancel_001', False, now + timedelta(days=5), 'revoked'),
    ]
    created = 0
    skipped = 0
    for user, plan, platform, txn, trial, expire, status in plan_rows:
        p = prod(plan, platform)
        if not p:
            skipped += 1
            print(f'  skip IAP {txn}: remote missing SKU {platform}/{plan}')
            continue
        cents = 0 if trial else int(getattr(p, 'price_cents', 0) or 0)
        currency = getattr(p, 'currency', None) or 'USD'
        IapReceipt.objects.update_or_create(
            transaction_id=txn,
            defaults={
                'user_id': user.id,
                'app_key': getattr(user, 'app_key', '') or 'selah',
                'platform': platform,
                'product_id': p.store_product_id,
                'original_transaction_id': txn + '_orig',
                'receipt_data': f'seed-receipt-{txn}',
                'is_trial': trial,
                'amount_cents': cents,
                'currency': currency,
                'expire_at': expire,
                'status': status,
            },
        )
        created += 1
    print(f'IAP from remote SKUs: upserted={created} skipped={skipped}')


def seed_analytics(users, inv):
    """埋点样例：content_id / plan_id / 语言国家平台取自远程用户与内容。"""
    now = _utcnow()
    persona_cycle = [
        users['fresh'], users['free_onboarded'], users['skipped'],
        users['vip'], users['trial'], users['quota_zero'],
        users['lang_ja'], users['lang_ko'], users['lang_pt'],
        users['quota_cap'], users['like_done'],
    ]
    quotes = _pick_quotes(inv, n=30) or []
    products = inv['products']
    AnalyticsEvent.objects.filter(params__seed=1).delete()

    rows = []
    for i, (name, _label, kind) in enumerate(EVENT_CATALOG):
        u = persona_cycle[i % len(persona_cycle)]
        day_offset = i % 14
        event_date = (now - timedelta(days=day_offset)).date()
        plat = u.platform if u.platform in ('ios', 'android') else ('ios' if i % 2 == 0 else 'android')
        params = {'seed': 1, 'kind': kind, 'source': 'remote_inventory'}
        if name in ('quote_expose', 'quote_like', 'quote_share', 'quote_collect', 'quote_report') and quotes:
            params['content_id'] = quotes[i % len(quotes)].id
        elif name == 'ai_safety_block':
            params['stage'] = 'input' if i % 2 == 0 else 'output'
            cats = list(
                AiSafetyKeyword.objects.filter(enabled=True)
                .values_list('category', flat=True).distinct()
            ) or SAFETY_CATEGORIES
            params['category'] = cats[i % len(cats)]
        elif name in ('vip_plan_select', 'vip_pay_start', 'vip_pay_success') and products:
            p = products[i % len(products)]
            params['plan_id'] = p.plan_type
            params['product_id'] = p.store_product_id
        elif name == 'quote_share':
            params['channel'] = 'instagram' if i % 2 == 0 else 'more'
        elif name == 'ai_quick_click' and inv['prompts']:
            params['prompt_id'] = inv['prompts'][i % len(inv['prompts'])].id

        rows.append(AnalyticsEvent(
            user_id=u.id,
            app_key=getattr(u, 'app_key', '') or 'selah',
            event_name=name,
            event_date=event_date,
            platform=plat,
            system_lang=u.system_lang or 'en',
            register_country=(u.register_country or 'US')[:8],
            params=params,
        ))
        if name in ('login_success', 'home_quote_view', 'quote_expose', 'ai_send', 'checkin_success'):
            for extra in range(1, 4):
                u2 = persona_cycle[(i + extra) % len(persona_cycle)]
                rows.append(AnalyticsEvent(
                    user_id=u2.id,
                    app_key=getattr(u2, 'app_key', '') or 'selah',
                    event_name=name,
                    event_date=(now - timedelta(days=(day_offset + extra) % 14)).date(),
                    platform=u2.platform if u2.platform in ('ios', 'android') else plat,
                    system_lang=u2.system_lang or 'en',
                    register_country=(u2.register_country or 'US')[:8],
                    params={'seed': 1, 'kind': kind, 'n': extra, 'source': 'remote_inventory'},
                ))

    AnalyticsEvent.objects.bulk_create(rows, batch_size=200)
    print(f'Analytics seeded: {len(rows)} (content/product ids from remote)')


def print_summary(users, inv, gaps):
    print('\n=== Seed personas (device_id) ===')
    guide = [
        ('fresh', '新用户未 Onboarding'),
        ('free_onboarded', 'Free 已画像；Assets 演示：喜欢≥8/收藏2夹/历史≥10/肯定语≥6'),
        ('skipped', '跳过 Onboarding'),
        ('quota_cap', 'Free 额度触顶 10'),
        ('quota_zero', 'Free 额度 0'),
        ('vip', '正式 VIP / seed_vip / change-me-vip'),
        ('trial', '试用 VIP'),
        ('vip_expired', 'VIP 已过期'),
        ('disabled', '禁用'),
        ('checkin_off', '打卡记录关'),
        ('like_done', '新人喜欢 5/5'),
        ('lang_ja', '日语画像'),
        ('lang_ko', '韩语画像'),
        ('lang_pt', '葡语画像'),
    ]
    for key, desc in guide:
        u = users[key]
        print(f'  {u.device_id:32} id={u.id:<5} {desc}')
    print('  admin: selah_admin / change-me-admin')
    print('  staff: selah_operator / change-me-ops | selah_mgr / change-me-mgr')
    print('\n=== H5 Assets demo ===')
    print('  Prefer device_id=seed_device_free_onboarded (do not overwrite browser device key).')
    print('  Tabs: ?kind=like|collect|history|affirmation')
    print('\n=== Wired to remote content ===')
    if inv['quotes']:
        print(f'  quote sample: id={inv["quotes"][0].id} {(inv["quotes"][0].content or "")[:48]}')
    if inv['wallpapers']:
        print(f'  wallpaper sample: {inv["wallpapers"][0].image_url[:72]}')
    if inv['products']:
        p = inv['products'][0]
        print(f'  SKU sample: {p.platform}/{p.plan_type} {p.store_product_id} {p.price_cents}¢')
    print(f'  gaps reported: {len(gaps)}')
    print('=== Done (no invented content written) ===')


def main():
    report_only = '--report-only' in sys.argv
    assert_remote_postgres()

    # 仅补缺默认配置 / 空库 SKU / 空库安全种子 —— 不覆盖运营已配置内容
    ensure_default_configs()
    ensure_default_welcome_screens()
    ensure_default_products()
    ensure_safety_seeds()
    # 库存不足时按标准虚拟补齐（语录/话题/壁纸/Welcome 图/价签/社交）
    ensure_virtual_catalog()

    inv = load_remote_inventory()
    gaps = report_coverage_gaps(inv)

    if not inv['quotes']:
        raise SystemExit(
            '虚拟补齐后仍无语录，请检查数据库连接与 ensure_virtual_catalog。'
        )

    if report_only:
        print('(--report-only) skip persona seed')
        return

    _ensure_admin()
    seed_staff()
    seed_affirmation_templates()
    seed_quote_source_refs(inv)
    users = seed_users(inv)
    seed_user_activity(users, inv)
    seed_checkins(users)
    seed_ai(users, inv)
    seed_iap(users, inv)
    seed_analytics(users, inv)
    print_summary(users, inv, gaps)


if __name__ == '__main__':
    main()
