#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""按 App 填充配置 + 冷启内容，并导出两边独立 bootstrap 快照。

保证 Selah / Manna 的 app-config、Welcome、Onboarding、Interstitial 在库内按
app_key 分行，导出后 `mobile/static/bootstrap` 与 `mobile-manna/static/bootstrap`
内容可区分。

用法（backend 目录）:
  ./uivsbe_env/bin/python scripts/seed_dual_app_packaging.py
  ./uivsbe_env/bin/python scripts/seed_dual_app_packaging.py --export-only
  ./uivsbe_env/bin/python scripts/seed_dual_app_packaging.py --skip-manna-ops
"""
from __future__ import annotations

import os
import subprocess
import sys

import django

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from models.models import (  # noqa: E402
    AffirmationTemplate,
    InterstitialScreen,
    MembershipProduct,
    OnboardingQuestion,
    Quote,
    Topic,
    WelcomeScreen,
)
from tools.membership_products import ensure_default_products  # noqa: E402
from tools.selah_enums import (  # noqa: E402
    default_onboarding_questions,
    default_onboarding_questions_2_0,
)
from tools.selah_quota import (  # noqa: E402
    DEFAULTS,
    MANNA_APP_KEY,
    MANNA_DEFAULTS,
    ensure_default_configs,
    ensure_manna_defaults,
    set_config,
)
from tools.selah_virtual_seed import (  # noqa: E402
    ensure_virtual_catalog,
    ensure_virtual_onboarding,
)

SELAH = 'selah'
MANNA = MANNA_APP_KEY

# Selah 维度显式配置（写入 app_key=selah，导出时优先于全局）
SELAH_PACK = {
    'brand_name': 'Selah',
    'support_email': 'support@selah.app',
    'url_instagram': 'https://instagram.com/selah',
    'url_tiktok': 'https://www.tiktok.com/@selah',
    'url_facebook': 'https://facebook.com/selah',
    'iap_ios_product_id': 'selah.vip.monthly',
    'iap_android_product_id': 'selah.vip.monthly',
    'iap_ios_yearly_product_id': 'selah.vip.yearly',
    'iap_android_yearly_product_id': 'selah.vip.yearly',
    'trial_days': 3,
    'vip_benefits': [
        'Unlock VIP topics & wallpapers',
        'Hide watermark on share cards',
        '{vip_daily_quota} AI messages / day',
    ],
    'vip_cta_text': 'Try for $0.00',
    'ai_greeting': (
        "Hi! I'm Selah — a gentle companion for prayer and affirmation. "
        "Share how you're feeling today."
    ),
    'welcome_enabled': True,
    'login_title': 'Sign in to Selah',
    'login_subtitle': 'Continue with Apple, Google, or a quick device login.',
    'selah_2_0_features_enabled': True,
    'trial_intro_enabled': True,
    'widget_teaser_enabled': True,
    'app_icon_teaser_enabled': True,
    'dynamic_wallpaper_tab_enabled': True,
    'manna_features_enabled': False,
    'versecard_brand': 'SELAH',
    'share_text_template': '{text} — {brand}',
    'stats_card_enabled': False,
    'free_quota_cap': 10,
    'vip_daily_quota': 100,
    'newcomer_like_goal': 5,
}

SELAH_WELCOME = {
    'title': 'Selah',
    'subtitle': 'Grow closer to God — daily affirmations & gentle devotion',
    'cta_text': 'Continue',
    'image_url': '/static/ui/login-bg.jpg',
    'version': 2,
    'sort_order': 0,
    'enabled': True,
}

MANNA_WELCOME = {
    'title': 'Manna',
    'subtitle': 'Daily Scripture · Amen · Grow in the Word',
    'cta_text': 'Begin',
    'image_url': '/static/ui/home/bg-279-364.png',
    'version': 2,
    'sort_order': 0,
    'enabled': True,
}

SELAH_INTERSTITIALS = [
    ('personalize_intro', 'Personalize Selah with a few details about your journey', '', 'Continue'),
    ('prefs_intro', 'Tune reminders and sounds to your rhythm', '', 'Continue'),
    ('push_nudge', "Don't miss your daily affirmation", 'Allow notifications so Selah can gently check in', 'Go to settings'),
    ('devotion_custom', 'Shape a quiet space for prayer and reflection', '', 'Continue'),
    ('value_edu', 'VIP unlocks themes, wallpapers, and more AI moments', '', 'Continue'),
    ('trial_intro', 'Enjoy 3 days of Selah Premium on us', '', 'Continue'),
]

MANNA_INTERSTITIALS = [
    ('personalize_intro', 'Tell us a bit about your Bible reading habits', '', 'Continue'),
    ('prefs_intro', 'Choose how Manna reminds you of daily verses', '', 'Continue'),
    ('push_nudge', "Stay close to today's Word", 'Enable notifications for daily Scripture', 'Go to settings'),
    ('devotion_custom', 'Walk devotion paths written for your season', '', 'Continue'),
    ('value_edu', 'Quizzes, stories, and badges help Scripture stick', '', 'Continue'),
    ('trial_intro', 'Try 7 days of Manna Premium — free for you', '', 'Start free week'),
]


def _upsert_configs(app_key: str, mapping: dict, remark: str) -> int:
    n = 0
    for key, value in mapping.items():
        set_config(key, value, remark=remark, language='', platform='', app_key=app_key)
        n += 1
    return n


def seed_configs() -> dict:
    ensure_default_configs()
    # 重置进程内 flag，允许本脚本强制写 manna 缺键后再覆盖
    import tools.selah_quota as quota
    quota._manna_defaults_ensured = False
    ensure_manna_defaults()

    selah_n = _upsert_configs(SELAH, SELAH_PACK, 'selah-pack')
    # 全局行也对齐 Selah 品牌（未绑 App 的回落）
    for k, v in SELAH_PACK.items():
        if k in DEFAULTS or k in (
            'login_title', 'login_subtitle', 'versecard_brand', 'share_text_template',
            'stats_card_enabled', 'manna_features_enabled',
        ):
            set_config(k, v, remark='selah-pack-global', language='', platform='', app_key='')

    manna_n = _upsert_configs(MANNA, {**MANNA_DEFAULTS, **{
        # 确保 MANNA_DEFAULTS 扩展项一定写入（覆盖旧 selah.vip.* 残留）
        'iap_ios_product_id': 'manna.vip.monthly',
        'iap_android_product_id': 'manna.vip.monthly',
        'iap_ios_yearly_product_id': 'manna.vip.yearly',
        'iap_android_yearly_product_id': 'manna.vip.yearly',
        'versecard_brand': 'MANNA',
        'vip_cta_text': 'Start free week',
    }}, 'manna-pack')
    return {'selah_keys': selah_n, 'manna_keys': manna_n}


def seed_welcome() -> dict:
    out = {}
    for app, payload in ((SELAH, SELAH_WELCOME), (MANNA, MANNA_WELCOME)):
        for lang in ('en', ''):
            row = WelcomeScreen.objects.filter(app_key=app, language=lang, platform='').order_by('id').first()
            if row:
                for k, v in payload.items():
                    setattr(row, k, v)
                row.save()
                out[f'{app}:{lang or "all"}'] = row.id
            else:
                row = WelcomeScreen.objects.create(app_key=app, language=lang, platform='', **payload)
                out[f'{app}:{lang or "all"}'] = row.id
    return out


def seed_onboarding() -> dict:
    """每 App 各写一套 en 问卷；Manna 标题偏圣经向。"""
    ensure_virtual_onboarding()  # 全局缺省
    counts = {}
    manna_title_map = {
        'faith_type': 'How would you describe your walk with Scripture?',
        'prayer_frequency': 'How often do you open the Bible?',
        'interest_tags': 'What themes do you want more of?',
        'display_basic_info': 'A few basics so Manna can greet you',
    }
    for app in (SELAH, MANNA):
        n = 0
        for i, q in enumerate(default_onboarding_questions()):
            title = q['title']
            if app == MANNA and q['question_key'] in manna_title_map:
                title = manna_title_map[q['question_key']]
            OnboardingQuestion.objects.update_or_create(
                question_key=q['question_key'],
                app_key=app,
                language='en',
                platform='',
                defaults={
                    'title': title,
                    'options': q.get('options') or [],
                    'sort_order': (i + 1) * 10,
                    'enabled': True,
                },
            )
            n += 1
        # Selah 2.0 扩展题：仅 Selah 启用；Manna 写但关闭
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
                    'enabled': app == SELAH,
                },
            )
            n += 1
        counts[app] = n
    return counts


def seed_interstitials() -> dict:
    counts = {}
    for app, seeds in ((SELAH, SELAH_INTERSTITIALS), (MANNA, MANNA_INTERSTITIALS)):
        n = 0
        for i, (kind, title, subtitle, cta) in enumerate(seeds):
            InterstitialScreen.objects.update_or_create(
                kind=kind,
                app_key=app,
                language='',
                platform='',
                defaults={
                    'title': title,
                    'subtitle': subtitle,
                    'cta_text': cta,
                    'image_url': '',
                    'version': 2,
                    'sort_order': (i + 1) * 10,
                    'enabled': True,
                },
            )
            n += 1
        counts[app] = n
    return counts


def seed_membership_skus() -> dict:
    """确保两边商店商品 ID 分行存在；iap_* 已由 seed_configs 按 App 写入。"""
    ensure_default_products()
    created = 0
    specs = [
        (SELAH, 'monthly', 'ios', 'selah.vip.monthly', 'Selah VIP Monthly', 30, 999),
        (SELAH, 'monthly', 'android', 'selah.vip.monthly', 'Selah VIP Monthly', 30, 999),
        (SELAH, 'yearly', 'ios', 'selah.vip.yearly', 'Selah VIP Yearly', 365, 3999),
        (SELAH, 'yearly', 'android', 'selah.vip.yearly', 'Selah VIP Yearly', 365, 3999),
        (MANNA, 'monthly', 'ios', 'manna.vip.monthly', 'Manna Premium Monthly', 30, 799),
        (MANNA, 'monthly', 'android', 'manna.vip.monthly', 'Manna Premium Monthly', 30, 799),
        (MANNA, 'yearly', 'ios', 'manna.vip.yearly', 'Manna Premium Yearly', 365, 4999),
        (MANNA, 'yearly', 'android', 'manna.vip.yearly', 'Manna Premium Yearly', 365, 4999),
    ]
    # MembershipProduct 按 app_key 分行；iap_* 由调用方按 App sync，勿全局覆盖
    for app, plan, plat, pid, title, days, cents in specs:
        _, was = MembershipProduct.objects.update_or_create(
            platform=plat,
            store_product_id=pid,
            defaults={
                'app_key': app,
                'plan_type': plan,
                'title': title,
                'price_display': f'${cents / 100:.2f}',
                'price_cents': cents,
                'currency': 'USD',
                'duration_days': days,
                'trial_days': 3 if app == SELAH else 7,
                'enabled': True,
                'sort_weight': (20 if plan == 'monthly' else 10) + (100 if app == SELAH else 0),
                'remark': f'pack:{app}',
            },
        )
        if was:
            created += 1
    from tools.membership_products import sync_iap_config_keys
    sync_iap_config_keys(app_key=SELAH)
    sync_iap_config_keys(app_key=MANNA)
    return {'upserted_slots': len(specs), 'created': created}


def seed_sample_content() -> dict:
    """各 App 各补少量可辨内容（语录 / 主题 / 肯定语模板）。"""
    out = {'quotes': 0, 'topics': 0, 'templates': 0}
    topic_specs = [
        (SELAH, 'Peace', 'peace', 'Quiet your heart with Selah'),
        (SELAH, 'Courage', 'courage', 'Stand firm in faith'),
        (MANNA, 'Daily Bread', 'bible', 'Scripture for every morning'),
        (MANNA, 'Amen Path', 'prayer', 'Walk through the Word'),
    ]
    for app, name, cat, desc in topic_specs:
        row = Topic.objects.filter(name=name, app_key=app, language='en', platform='', is_deleted=False).first()
        if row:
            row.category = cat
            row.description = desc
            row.status = 1
            row.save(update_fields=['category', 'description', 'status', 'updated_at'])
        else:
            Topic.objects.create(
                name=name, app_key=app, language='en', platform='',
                category=cat, description=desc, cover_url='',
                sort_weight=10, status=1, is_deleted=False, is_vip=False,
            )
        out['topics'] += 1

    quote_specs = [
        (SELAH, 'Be still, and know that I am God.', ['peace'], ['christian']),
        (SELAH, 'The Lord is my shepherd; I shall not want.', ['comfort'], ['christian']),
        (MANNA, 'Man shall not live by bread alone, but by every word from God.', ['bible'], ['christian']),
        (MANNA, 'Your word is a lamp to my feet and a light to my path.', ['bible'], ['christian']),
    ]
    for app, content, interest, faith in quote_specs:
        row = Quote.objects.filter(content=content, app_key=app, language='en', platform='', is_deleted=False).first()
        if row:
            row.interest_tags = interest
            row.faith_tags = faith
            row.status = 1
            row.in_cold_start_pool = True
            row.save(update_fields=['interest_tags', 'faith_tags', 'status', 'in_cold_start_pool', 'updated_at'])
        else:
            Quote.objects.create(
                content=content, app_key=app, language='en', platform='',
                interest_tags=interest, faith_tags=faith, source_ref='',
                sort_weight=10, status=1, is_deleted=False, is_vip=False,
                in_cold_start_pool=True,
            )
        out['quotes'] += 1

    tpl_specs = [
        (SELAH, 'Today I choose peace over worry.'),
        (SELAH, 'I am held by a love that never fails.'),
        (MANNA, 'I will hide Your word in my heart today.'),
        (MANNA, 'Scripture lights the next step I take.'),
    ]
    for app, content in tpl_specs:
        row = AffirmationTemplate.objects.filter(
            content=content, app_key=app, language='en', platform='', is_deleted=False,
        ).first()
        if row:
            row.status = 1
            row.save(update_fields=['status', 'updated_at'])
        else:
            AffirmationTemplate.objects.create(
                content=content, app_key=app, language='en', platform='',
                sort_weight=10, status=1, is_deleted=False,
            )
        out['templates'] += 1
    return out


def run_export() -> None:
    py = sys.executable
    scripts_dir = os.path.join(BASE, 'scripts')
    for script in ('export_selah_bootstrap.py', 'export_manna_bootstrap.py'):
        path = os.path.join(scripts_dir, script)
        if not os.path.isfile(path):
            raise SystemExit(f'missing export script: {path}')
        print(f'--- running {script} ---', flush=True)
        subprocess.check_call([py, path], cwd=BASE)


def main():
    export_only = '--export-only' in sys.argv
    skip_manna_ops = '--skip-manna-ops' in sys.argv

    if not export_only:
        print('=== seed configs ===')
        print(seed_configs())
        print('=== seed welcome / onboarding / interstitials ===')
        print('welcome', seed_welcome())
        print('onboarding', seed_onboarding())
        print('interstitials', seed_interstitials())
        print('=== membership SKUs ===')
        print(seed_membership_skus())
        print('=== sample content ===')
        print(seed_sample_content())
        print('=== selah virtual catalog (fill gaps) ===')
        print(ensure_virtual_catalog())
        if not skip_manna_ops:
            print('=== manna ops seed ===')
            manna_script = os.path.join(BASE, 'scripts', 'seed_manna.py')
            subprocess.check_call([sys.executable, manna_script], cwd=BASE)

    print('=== export bootstraps ===')
    run_export()
    print('done')


if __name__ == '__main__':
    main()
