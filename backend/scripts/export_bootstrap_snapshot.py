#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
发版前从运营库导出公开配置快照，写入对应 App 的 `static/bootstrap/`。

Selah 与 Manna **独立目录、独立 scope**，禁止交叉写入：

  Selah → frontend/mobile/static/bootstrap        （--preset selah）
  Manna → frontend/mobile-manna/static/bootstrap  （--preset manna）

推荐用法（在 backend 目录）:
  ./uivsbe_env/bin/python scripts/export_bootstrap_snapshot.py --preset selah
  ./uivsbe_env/bin/python scripts/export_bootstrap_snapshot.py --preset manna

或调用薄封装（更不易混）:
  ./uivsbe_env/bin/python scripts/export_selah_bootstrap.py
  ./uivsbe_env/bin/python scripts/export_manna_bootstrap.py

导出内容（与各端 pageCache 形状对齐）:
  - app-config.json
  - welcome.json
  - onboarding-questions.json  ({ revision, questions })
  - interstitials.json         ({ kind: payload })
  - manifest.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import django

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = BACKEND_ROOT.parent / 'frontend'
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from tools.selah_enums import (  # noqa: E402
    AGE_RANGES,
    GENDERS,
    ONBOARDING_KEY_BASIC_INFO,
    ONBOARDING_KEY_BASIC_INFO_LEGACY,
    as_options,
    interstitial_kind_values,
)
from tools.selah_quota import (  # noqa: E402
    ensure_default_configs,
    ensure_default_welcome_screens,
    filter_scoped_rows,
    get_configs,
    set_config_context,
)

# 与 ContentViewSet.app_config 白名单保持一致（含 Manna / 登录展示键）
APP_CONFIG_KEYS = [
    'brand_name', 'legal_entity_name', 'url_privacy', 'url_terms', 'url_help',
    'url_download', 'url_subscription', 'url_instagram', 'url_tiktok', 'url_facebook',
    'support_email', 'checkin_auto_popup', 'cold_start_interest_ratio',
    'cold_start_ops_ratio', 'free_quota_cap', 'vip_daily_quota', 'checkin_reward',
    'iap_ios_product_id', 'iap_android_product_id',
    'iap_ios_yearly_product_id', 'iap_android_yearly_product_id', 'trial_days',
    'vip_benefits', 'vip_cta_text', 'ai_greeting', 'streak_miss_reset_days',
    'welcome_enabled', 'newcomer_like_goal',
    'newcomer_intro_title', 'newcomer_intro_body',
    'newcomer_done_text', 'newcomer_congrats_text',
    'selah_2_0_features_enabled', 'trial_intro_enabled',
    'widget_teaser_enabled', 'app_icon_teaser_enabled',
    'dynamic_wallpaper_tab_enabled',
    'manna_features_enabled', 'manna_vip_enabled', 'versecard_brand',
    'share_text_template', 'stats_card_enabled', 'manna_newcomer_grant',
    'login_title', 'login_subtitle',
]

# 包内不写死本机 / 品牌占位 legal URL；空串交给运行时 app-config 补全
_LEGAL_URL_HOST_PREFIXES = (
    'https://selah.app/',
    'http://selah.app/',
    'https://manna.app/',
    'http://manna.app/',
)

PRESETS = {
    'selah': {
        'label': 'Selah',
        'app_key': 'selah',
        'out': FRONTEND_ROOT / 'mobile' / 'static' / 'bootstrap',
    },
    'manna': {
        'label': 'Manna',
        'app_key': 'manna',
        'out': FRONTEND_ROOT / 'mobile-manna' / 'static' / 'bootstrap',
    },
}


def _iso(dt) -> str:
    if not dt:
        return '0'
    if getattr(dt, 'tzinfo', None) is None:
        return dt.isoformat() + 'Z'
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _blank_packaged_legal_url(val: str) -> str:
    text = (val or '').strip()
    if not text:
        return ''
    lower = text.lower()
    for prefix in _LEGAL_URL_HOST_PREFIXES:
        if lower.startswith(prefix):
            return ''
    return text


def build_app_config():
    from models.models import AppConfig
    ensure_default_configs()
    data = get_configs(APP_CONFIG_KEYS)
    data['membership_products'] = []
    for key in ('url_privacy', 'url_terms', 'url_help', 'url_download', 'url_subscription'):
        data[key] = _blank_packaged_legal_url(data.get(key) or '')
    mx = (
        AppConfig.objects.filter(key__in=APP_CONFIG_KEYS)
        .order_by('-updated_at')
        .values_list('updated_at', flat=True)
        .first()
    )
    data['revision'] = _iso(mx)
    return data


def build_welcome(lang: str, plat: str, app: str):
    from models.models import WelcomeScreen
    from tools.selah_quota import get_config
    from tools.selah_virtual_seed import ensure_virtual_welcome

    ensure_default_welcome_screens()
    ensure_virtual_welcome()
    if not get_config('welcome_enabled', True):
        return {}
    rows = list(WelcomeScreen.objects.filter(enabled=True).order_by('sort_order', 'id'))
    rows = filter_scoped_rows(rows, lang, plat, group_key=None, app_key=app)
    if not rows:
        return {}
    r = rows[0]
    return {
        'id': r.id,
        'title': r.title,
        'subtitle': r.subtitle or '',
        'cta_text': r.cta_text or 'Continue',
        'image_url': r.image_url or '',
        'version': int(r.version or 1),
    }


def build_interstitials(lang: str, plat: str, app: str):
    from models.models import InterstitialScreen
    from tools.selah_virtual_seed import ensure_virtual_interstitials

    ensure_virtual_interstitials()
    out = {}
    for kind in interstitial_kind_values():
        rows = list(
            InterstitialScreen.objects.filter(enabled=True, kind=kind).order_by('sort_order', 'id')
        )
        rows = filter_scoped_rows(rows, lang, plat, group_key=None, app_key=app)
        if not rows:
            continue
        r = rows[0]
        out[kind] = {
            'id': r.id,
            'kind': r.kind,
            'title': r.title,
            'subtitle': r.subtitle or '',
            'cta_text': r.cta_text or 'Continue',
            'image_url': r.image_url or '',
            'version': int(r.version or 1),
        }
    return out


def build_onboarding(lang: str, plat: str, app: str):
    from models.models import OnboardingQuestion
    from tools.selah_quota import normalize_app_key

    rows = list(OnboardingQuestion.objects.filter(enabled=True).order_by('sort_order', 'id'))
    rows = filter_scoped_rows(
        rows, lang, plat, group_key='question_key', app_key=normalize_app_key(app),
    )
    mx = (
        OnboardingQuestion.objects.filter(enabled=True)
        .order_by('-updated_at')
        .values_list('updated_at', flat=True)
        .first()
    )
    revision = _iso(mx)
    if not rows:
        return {'revision': revision or '0', 'questions': []}
    questions = []
    for r in rows:
        item = {
            'question_key': r.question_key,
            'title': r.title,
            'options': r.options or [],
            'sort_order': int(getattr(r, 'sort_order', 0) or 0),
        }
        key = r.question_key
        if key in (ONBOARDING_KEY_BASIC_INFO, ONBOARDING_KEY_BASIC_INFO_LEGACY):
            if key == ONBOARDING_KEY_BASIC_INFO_LEGACY:
                item['question_key'] = ONBOARDING_KEY_BASIC_INFO
            item['meta'] = {
                'genders': as_options(GENDERS),
                'age_ranges': as_options(AGE_RANGES),
            }
        questions.append(item)
    return {'revision': revision, 'questions': questions}


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _resolve_target(args) -> tuple[str, str, Path]:
    """返回 (label, app_key, out_dir)。preset 与显式 --out/--app-key 可叠加；显式优先。"""
    label = 'custom'
    app_key = args.app_key or ''
    out = Path(args.out).resolve() if args.out else None

    if args.preset:
        preset = PRESETS[args.preset]
        label = preset['label']
        if not args.app_key:
            app_key = preset['app_key']
        if not args.out:
            out = Path(preset['out']).resolve()

    if out is None:
        # 未指定 preset / out 时默认 Selah 目录（兼容旧命令）；建议改用 --preset
        label = 'Selah'
        out = Path(PRESETS['selah']['out']).resolve()
        if not args.app_key:
            app_key = PRESETS['selah']['app_key']

    # 交叉写入护栏：preset 与 out 不得指向另一 App
    norm = str(out).replace('\\', '/')
    if args.preset == 'selah' and 'mobile-manna' in norm:
        raise SystemExit('Refusing: --preset selah must not write into mobile-manna/')
    if args.preset == 'manna' and 'mobile-manna' not in norm:
        raise SystemExit('Refusing: --preset manna must write into mobile-manna/static/bootstrap')

    return label, app_key, out


def main():
    parser = argparse.ArgumentParser(
        description='Export public bootstrap snapshots (Selah / Manna; separate dirs)',
    )
    parser.add_argument(
        '--preset',
        choices=sorted(PRESETS.keys()),
        default=None,
        help='selah → mobile/static/bootstrap；manna → mobile-manna/static/bootstrap',
    )
    parser.add_argument('--lang', default='en')
    parser.add_argument('--platform', default='')
    parser.add_argument('--app-key', default='', help='覆盖 preset 的 app_key（一般不必）')
    parser.add_argument(
        '--out',
        default='',
        help='覆盖输出目录（一般不必；勿把 Selah/Manna 目录写反）',
    )
    args = parser.parse_args()

    label, app_key, out = _resolve_target(args)

    # set_config_context(language, platform, app_key)
    set_config_context(args.lang, args.platform, app_key)

    app_config = build_app_config()
    welcome = build_welcome(args.lang, args.platform, app_key)
    onboarding = build_onboarding(args.lang, args.platform, app_key)
    interstitials = build_interstitials(args.lang, args.platform, app_key)

    write_json(out / 'app-config.json', app_config)
    write_json(out / 'welcome.json', welcome)
    write_json(out / 'onboarding-questions.json', onboarding)
    write_json(out / 'interstitials.json', interstitials)
    manifest = {
        'exported_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'app': label.lower(),
        'scope': {
            'language': args.lang,
            'platform': args.platform,
            'app_key': app_key,
        },
        'app_config_revision': app_config.get('revision'),
        'welcome_version': welcome.get('version', 0) if welcome else 0,
        'onboarding_revision': onboarding.get('revision'),
        'onboarding_count': len(onboarding.get('questions') or []),
        'interstitial_versions': {k: v.get('version', 1) for k, v in interstitials.items()},
    }
    write_json(out / 'manifest.json', manifest)
    print(f'=== Bootstrap export: {label} ===')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f'Wrote {label} bootstrap → {out}')


if __name__ == '__main__':
    main()
