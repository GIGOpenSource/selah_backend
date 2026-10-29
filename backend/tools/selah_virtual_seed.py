#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
Selah 虚拟运营目录：库空或缺资源时按产品标准补齐可演示数据。

原则：
- 只补缺，不覆盖运营已写入的非空字段（除非 force=True）
- 图片用可公网访问的虚拟 URL（placehold.co），tags 含 virtual/seed 便于日后替换
- 语录覆盖 INTEREST_TAGS + 冷启池；话题覆盖 TOPIC_CATEGORIES；壁纸 ≥10 张免费

调用：
- scripts/seed_virtual_catalog.py
- seed_selah 画像种子前
- App 关键读接口空库时懒加载（ensure_virtual_catalog）
"""
import logging
from urllib.parse import quote

from django.db import transaction

from models.models import (
    MembershipProduct,
    OnboardingQuestion,
    Quote,
    Topic,
    Wallpaper,
    WelcomeScreen,
    InterstitialScreen,
)
from tools.selah_enums import (
    INTEREST_TAGS,
    TOPIC_CATEGORIES,
    default_onboarding_questions,
    default_onboarding_questions_2_0,
    INTERSTITIAL_KINDS,
    values_of,
)
from tools.selah_quota import ensure_default_configs, ensure_default_welcome_screens, set_config, get_config

logger = logging.getLogger(__name__)

VIRTUAL_TAG = 'virtual'

# 英文肯定语样例（覆盖兴趣标签；可重复拼冷启池）
SAMPLE_QUOTES = [
    ('Peace begins when I pause and breathe with God.', ['peace', 'prayer']),
    ('I am held by a love that does not let go.', ['healing', 'hope']),
    ('Courage grows one quiet yes at a time.', ['courage', 'growth']),
    ('Gratitude turns what I have into enough.', ['gratitude', 'peace']),
    ('My family is wrapped in gentle mercy today.', ['family', 'prayer']),
    ('Anxiety loosens when I cast my cares upward.', ['anxiety', 'peace']),
    ('Work becomes worship when I offer my best.', ['work', 'hope']),
    ('Hope is the light that finds me in the dark.', ['hope', 'healing']),
    ('Relationships heal when kindness leads.', ['relationships', 'healing']),
    ('Prayer is the conversation that steadies me.', ['prayer', 'peace']),
    ('I choose calm over the storm inside.', ['peace', 'anxiety']),
    ('Every morning is a new mercy.', ['gratitude', 'hope']),
    ('Faith is trust with unfinished answers.', ['hope', 'prayer']),
    ('I release what I cannot carry alone.', ['anxiety', 'healing']),
    ('Love is patient with my becoming.', ['relationships', 'family']),
    ('Strength rises when I rest in God.', ['courage', 'peace']),
    ('Purpose unfolds one faithful step.', ['work', 'hope']),
    ('Healing is not linear, and that is okay.', ['healing', 'hope']),
    ('I speak life over my home today.', ['family', 'prayer']),
    ('Stillness is a sacred gift I accept.', ['peace', 'prayer']),
]

TOPIC_SEEDS = [
    ('Morning Peace', 'calm', 'Start the day with quiet affirmations.'),
    ('Steady Faith', 'faith', 'Trust when the path is unclear.'),
    ('Gentle Courage', 'growth', 'Small brave steps forward.'),
    ('Anxious Hearts', 'emotion', 'Breathe through worry with hope.'),
    ('Home & Family', 'life', 'Bless the people you love.'),
    ('Season of Hope', 'seasonal', 'Light for this season.'),
    ('Work & Calling', 'life', 'Purpose in daily work.'),
    ('Healing Road', 'emotion', 'Soft words for tender days.'),
    ('Gratitude Trail', 'calm', 'Notice the good already here.'),
    ('Prayer Closet', 'faith', 'Simple prayers for real life.'),
    ('Deep Rest', 'calm', 'Permission to slow down.'),
    ('Brave Love', 'growth', 'Kindness that stays.'),
]


def _placeholder(w, h, text, bg='B6BADF', fg='191919'):
    """公网可访问的虚拟图（无需 COS）。"""
    label = quote(str(text)[:40])
    return f'https://placehold.co/{w}x{h}/{bg}/{fg}/png?text={label}'


def ensure_virtual_prices():
    """补齐会员 SKU 展示价（不覆盖已有非空 price_display）。"""
    defaults = {
        'monthly': ('$4.99', 499),
        'yearly': ('$39.99', 3999),
    }
    n = 0
    for p in MembershipProduct.objects.filter(enabled=True):
        price_txt, cents = defaults.get(p.plan_type, ('$4.99', 499))
        changed = []
        if not (p.price_display or '').strip():
            p.price_display = price_txt
            changed.append('price_display')
        if not int(getattr(p, 'price_cents', 0) or 0):
            p.price_cents = cents
            changed.append('price_cents')
        if not (getattr(p, 'currency', None) or '').strip():
            p.currency = 'USD'
            changed.append('currency')
        if p.trial_days is None:
            p.trial_days = int(get_config('trial_days', 3))
            changed.append('trial_days')
        if changed:
            fields = list(changed)
            if hasattr(p, 'updated_at'):
                fields.append('updated_at')
            p.save(update_fields=fields)
            n += 1
    return n


def ensure_virtual_social_and_benefits():
    """社交链与 VIP 权益写入 AppConfig（仅缺省 key）。"""
    ensure_default_configs()
    extras = {
        'url_instagram': 'https://instagram.com/selah',
        'url_tiktok': 'https://www.tiktok.com/@selah',
        'url_facebook': 'https://facebook.com/selah',
        'url_download': 'https://selah.app/download',
        'vip_benefits': [
            'Unlock VIP topics & wallpapers',
            'Hide watermark on share cards',
            '{vip_daily_quota} AI messages / day',
        ],
        'vip_cta_text': 'Try for $0.00',
        'ai_greeting': (
            "Hi! I'm here for you. Go ahead and share how you're feeling or what you need today."
        ),
    }
    for k, v in extras.items():
        # 仅当库中无该全局 key 时写入（与 ensure_default_configs 一致）
        from models.models import AppConfig
        if not AppConfig.objects.filter(key=k, app_key='', language='', platform='').exists():
            set_config(k, v, remark='virtual seed')
    from tools.selah_quota import repair_vip_benefits_placeholder
    repair_vip_benefits_placeholder()
    return True


def ensure_virtual_welcome():
    """Welcome 文案 + 虚拟背景图。"""
    ensure_default_welcome_screens()
    n = 0
    for row in WelcomeScreen.objects.filter(enabled=True):
        changed = []
        if not (row.title or '').strip():
            row.title = 'Selah'
            changed.append('title')
        if not (row.subtitle or '').strip():
            row.subtitle = 'Grow closer to God'
            changed.append('subtitle')
        if not (row.cta_text or '').strip():
            row.cta_text = 'Continue'
            changed.append('cta_text')
        if not (row.image_url or '').strip():
            row.image_url = _placeholder(750, 1334, 'Selah', bg='B6BADF', fg='FFF0D9')
            changed.append('image_url')
        if changed:
            row.save(update_fields=changed + ['updated_at'])
            n += 1
    return n


def ensure_virtual_onboarding():
    """把标准四题 + 2.0 扩展题写入/更新 DB（按 selah/manna 各一份，禁用错误的独立 age_range 题）。"""
    OnboardingQuestion.objects.filter(question_key='age_range').update(enabled=False)
    touched = 0
    for app in ('selah', 'manna'):
        for i, q in enumerate(default_onboarding_questions()):
            OnboardingQuestion.objects.update_or_create(
                question_key=q['question_key'],
                app_key=app,
                language='en',
                platform='',
                defaults={
                    'title': q['title'],
                    'options': q.get('options') or [],
                    'sort_order': (i + 1) * 10,
                    'enabled': True,
                },
            )
            touched += 1
        for q in default_onboarding_questions_2_0():
            OnboardingQuestion.objects.update_or_create(
                question_key=q['question_key'],
                app_key=app,
                language='en',
                platform='',
                defaults={
                    'title': q['title'],
                    'options': q.get('options') or [],
                    'sort_order': int(q.get('sort_order') or 100),
                    'enabled': False,  # 2.0 种子默认关闭；运营开启后可见
                },
            )
            touched += 1
    return touched


def ensure_virtual_interstitials():
    """补齐 6 种 kind 教育夹层（Figma 136:* 文案；默认启用；按 App 各一份）。"""
    # node → kind：22940 personalize / 22960 prefs / 21745 push / 21811 devotion / 22652 value / 22689 trial
    seeds = [
        (
            'personalize_intro',
            'Personalize the app by sharing a few details about yourself',
            '',
            'Continue',
        ),
        (
            'prefs_intro',
            'Set up the app according to your preferences',
            '',
            'Continue',
        ),
        (
            'push_nudge',
            "Don't miss your daily verses!",
            "Open settings to allow notifications, it'll have a big impact in your life",
            'Go to settings',
        ),
        (
            'devotion_custom',
            'Customize the app for your spiritual journey',
            '',
            'Continue',
        ),
        (
            'value_edu',
            'Get hourly verses, quotes, and prayers to stay close to God throughout the day',
            '',
            'Continue',
        ),
        (
            'trial_intro',
            'We offer 3 days of Premium access for free, just for you',
            '',
            'Continue',
        ),
    ]
    n = 0
    for app in ('selah', 'manna'):
        for i, (kind, title, subtitle, cta) in enumerate(seeds):
            obj, created = InterstitialScreen.objects.get_or_create(
                kind=kind,
                app_key=app,
                language='',
                platform='',
                defaults={
                    'title': title,
                    'subtitle': subtitle,
                    'cta_text': cta,
                    'image_url': '',
                    'version': 1,
                    'sort_order': (i + 1) * 10,
                    'enabled': True,
                },
            )
            if created:
                n += 1
            elif not (obj.title or '').strip():
                obj.title = title
                obj.subtitle = subtitle
                obj.cta_text = cta
                obj.enabled = True
                obj.save(update_fields=['title', 'subtitle', 'cta_text', 'enabled', 'updated_at'])
                n += 1
    enabled_kinds = set(
        InterstitialScreen.objects.filter(enabled=True).values_list('kind', flat=True)
    )
    if len(enabled_kinds) < 4:
        for kind, _ in INTERSTITIAL_KINDS[:4]:
            InterstitialScreen.objects.filter(kind=kind).update(enabled=True)
    return n


def ensure_virtual_topics():
    """话题 ≥12，无封面则补虚拟图。"""
    n = Topic.objects.filter(is_deleted=False, status=1).count()
    created = 0
    if n < 12:
        existing = set(
            Topic.objects.filter(is_deleted=False).values_list('name', flat=True)
        )
        for name, cat, desc in TOPIC_SEEDS:
            if n + created >= 12:
                break
            if name in existing:
                continue
            Topic.objects.create(
                name=name,
                category=cat,
                description=desc,
                cover_url=_placeholder(600, 600, name, bg='C5C3E8', fg='191919'),
                is_vip=(created % 5 == 0),
                sort_weight=100 - created,
                status=1,
                app_key='selah',
            )
            created += 1
    # 补封面
    filled = 0
    for i, t in enumerate(Topic.objects.filter(is_deleted=False, status=1)):
        if (t.cover_url or '').strip():
            continue
        t.cover_url = _placeholder(600, 600, t.name or f'Topic{i}', bg='C5C3E8', fg='191919')
        t.save(update_fields=['cover_url', 'updated_at'])
        filled += 1
    return created, filled


def ensure_virtual_wallpapers():
    """壁纸：至少 12 张，其中 ≥10 免费。"""
    qs = Wallpaper.objects.filter(is_deleted=False, status=1)
    n = qs.count()
    free_n = qs.filter(is_vip=False).count()
    created = 0
    need = max(0, 12 - n)
    need_free = max(0, 10 - free_n)
    total_need = max(need, need_free)
    colors = ['B6BADF', 'FFF0D9', 'F5DFBC', 'C5C3E8', 'FBEED8', 'E8E6F5']
    for i in range(total_need):
        # 优先补免费；免费满 10 后再允许 VIP
        make_vip = free_n >= 10 and (i % 4 == 0)
        if not make_vip:
            free_n += 1
        Wallpaper.objects.create(
            image_url=_placeholder(750, 1334, f'Selah {n + i + 1}', bg=colors[i % len(colors)], fg='191919'),
            is_vip=make_vip,
            tags=[VIRTUAL_TAG, 'seed'],
            sort_weight=80 - i,
            status=1,
            app_key='selah',
        )
        created += 1
    return created


def ensure_virtual_quotes():
    """
    语录：≥50 条；冷启池 ≥15；每个 INTEREST_TAGS 至少 1 条。
    """
    tags = values_of(INTEREST_TAGS)
    topics = list(Topic.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', 'id'))
    created = 0

    for tag in tags:
        exists = Quote.objects.filter(
            is_deleted=False, status=1, interest_tags__contains=[tag]
        ).exists()
        if exists:
            continue
        hit = next(((c, ts) for c, ts in SAMPLE_QUOTES if tag in ts), None)
        if hit:
            content, its = hit
        else:
            content, its = (f'Grace meets me in {tag}.', [tag])
        tid = topics[created % len(topics)].id if topics else None
        Quote.objects.create(
            content=content,
            language='en',
            interest_tags=list(dict.fromkeys([tag, *its]))[:4],
            faith_tags=['learning'],
            in_cold_start_pool=True,
            sort_weight=50,
            topic_id=tid,
            status=1,
            app_key='selah',
        )
        created += 1

    total = Quote.objects.filter(is_deleted=False, status=1).count()
    cold = Quote.objects.filter(is_deleted=False, status=1, in_cold_start_pool=True).count()
    while total < 50 or cold < 15:
        idx = total
        content, its = SAMPLE_QUOTES[idx % len(SAMPLE_QUOTES)]
        body = content if idx < len(SAMPLE_QUOTES) else f'{content} ({idx + 1})'
        tid = topics[idx % len(topics)].id if topics else None
        Quote.objects.create(
            content=body,
            language='en',
            interest_tags=its,
            faith_tags=['learning'],
            in_cold_start_pool=cold < 40,
            sort_weight=40 - (idx % 20),
            topic_id=tid,
            status=1,
            is_vip=(idx % 11 == 0),
            app_key='selah',
        )
        created += 1
        total += 1
        if cold < 40:
            cold += 1
    return created


@transaction.atomic
def ensure_virtual_catalog(force_log=True):
    """一站式补齐。可重复执行。"""
    from tools.membership_products import ensure_default_products
    from Apps.views.ai.view import ensure_safety_seeds

    ensure_default_configs()
    ensure_default_products()
    ensure_safety_seeds()
    ensure_virtual_social_and_benefits()
    ensure_virtual_prices()
    w = ensure_virtual_welcome()
    o = ensure_virtual_onboarding()
    inter = ensure_virtual_interstitials()
    tc, tf = ensure_virtual_topics()
    wp = ensure_virtual_wallpapers()
    q = ensure_virtual_quotes()
    summary = {
        'welcome_updated': w,
        'onboarding_touched': o,
        'interstitials_created': inter,
        'topics_created': tc,
        'topic_covers_filled': tf,
        'wallpapers_created': wp,
        'quotes_created': q,
    }
    if force_log:
        logger.info('ensure_virtual_catalog %s', summary)
        print('virtual catalog:', summary)
    return summary
