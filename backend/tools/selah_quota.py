#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
额度账本与运营配置读取。

职责：
- AppConfig：按 app_key × language × platform 级联回退读配置；DEFAULTS 为种子/兜底
- 额度：免费额度（free_quota + cap）与 VIP 日额度（本地 0 点刷新）扣减/入账
- 用户公开字段：user_public_dict 供 App 登录/资料接口复用

请求级 app/语言/平台由 IsTokenValid 或 set_config_context_from_request 写入 ContextVar。

配置回退（app × language × platform，越前越优先）：
  1. (app, lang, plat)
  2. (app, lang, '')
  3. (app, '', plat)
  4. (app, '', '')
  5. ('', lang, plat)
  6. ('', lang, '')
  7. ('', '', plat)
  8. ('', '', '')              — 全局种子行；仍无则 DEFAULTS[key]

账本规则摘要：
- 免费用户：扣/加 free_quota；加分受 free_quota_cap 封顶；新人礼包仅一次
- VIP/试用：扣 vip_daily_remaining；按用户 timezone 本地日切刷新；过期自动降免费
"""
import logging
from contextvars import ContextVar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone as dj_tz

from models.models import AppConfig, User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 请求级 ContextVar：由 permissions.IsTokenValid / set_config_context_from_request 写入
# get_config / get_configs 在参数为 None 时读这里
# 管理员或未登录应 clear，避免串用上一次请求的作用域
# ---------------------------------------------------------------------------
_cfg_app_key: ContextVar[str] = ContextVar('cfg_app_key', default='')
_cfg_language: ContextVar[str] = ContextVar('cfg_language', default='')
_cfg_platform: ContextVar[str] = ContextVar('cfg_platform', default='')
_cfg_degraded: ContextVar[bool] = ContextVar('cfg_degraded', default=False)

# 运营台可选 scope；空串 '' 表示「全部」
OPS_APP_KEYS = ('', 'selah', 'manna')
OPS_LANGUAGES = ('', 'en', 'zh', 'es', 'ja', 'ko', 'pt', 'ar', 'ru', 'fr', 'de', 'fil', 'it')
OPS_PLATFORMS = ('', 'ios', 'android')
DEFAULT_APP_KEY = 'selah'
MANNA_APP_KEY = 'manna'

# Manna 专属配置（写入 app_key=manna 行；不污染全局 Selah DEFAULTS）
MANNA_DEFAULTS = {
    'brand_name': 'Manna',
    'manna_features_enabled': True,
    'manna_newcomer_grant': 200,
    'manna_quiz_pass_reward': 30,
    'manna_quiz_fail_penalty': 10,
    'manna_in_bible_cost': 10,
    'manna_quiz_review_cost': 10,
    'manna_prayer_amen_reward': 10,
    'manna_devotion_amen_reward': 10,
    'manna_bible_read_reward': 10,
    'manna_bible_read_seconds': 300,
    'manna_bible_read_daily_cap': 2,
    'manna_home_amen_reward': 0,
    'manna_quiz_require_all_correct': True,
    'manna_vip_enabled': False,
    'versecard_brand': 'MANNA',
    'share_text_template': '{text} — {reference} · {brand}',
    'home_embed_quiz_manna_reward': 0,
    'stats_card_enabled': True,
    'welcome_enabled': True,
    'login_title': 'Sign in to Manna',
    'login_subtitle': 'Continue with Apple, Google, or a quick device login.',
    'support_email': 'support@manna.app',
    'support_share_label': 'Share Manna',
    'support_rate_label': 'Rate Manna',
    'url_privacy': '',
    'url_terms': '',
    'url_help': '',
    'url_download': '',
    'url_subscription': '',
    'url_instagram': 'https://instagram.com/manna',
    'url_tiktok': 'https://www.tiktok.com/@manna',
    'url_facebook': 'https://facebook.com/manna',
    'iap_ios_product_id': 'manna.vip.monthly',
    'iap_android_product_id': 'manna.vip.monthly',
    'iap_ios_yearly_product_id': 'manna.vip.yearly',
    'iap_android_yearly_product_id': 'manna.vip.yearly',
    'trial_days': 7,
    'vip_benefits': [
        'Daily Scripture & devotion paths',
        'Unlock quiz stories & badges',
        '{vip_daily_quota} AI reflections / day',
    ],
    'vip_cta_text': 'Start free week',
    'ai_greeting': (
        "Hi, I'm Manna — here with Scripture and a listening ear. "
        "Share what's on your heart today."
    ),
    'selah_2_0_features_enabled': False,
    'trial_intro_enabled': True,
    'widget_teaser_enabled': False,
    'app_icon_teaser_enabled': False,
    'dynamic_wallpaper_tab_enabled': False,
    'free_quota_cap': 15,
    'vip_daily_quota': 80,
    'newcomer_like_goal': 3,
    # 今日经文 / 祷告轮换：date=生效日或日历序；daily_random=当日稳定随机；sort=按排序；fixed=指定 ID
    'manna_verse_rotate': 'date',
    'manna_prayer_rotate': 'date',
    'manna_verse_fixed_id': 0,
    'manna_prayer_morning_id': 0,
    'manna_prayer_night_id': 0,
    # 听经底图（公开）；音频根目录仅服务端（空则用环境变量 BIBLE_AUDIO_ROOT）
    'bible_listen_bg_url': '/static/ui/bible/listen/bg-listen.png',
    # Quiz 枢纽日/夜间底图（空则 App 用本地无字场景池；勿填首页带字经文卡）
    'manna_quiz_hub_bg_url': '',
    'manna_quiz_hub_bg_night_url': '',
    'bible_audio_root': '',
    'translate_enabled': True,
    # 认证 / 会话 / IAP 门禁（密钥仍走环境变量）
    'device_login_enabled': True,
    # 提审临时：快捷登录默认 VIP；测支付期间关，过审/提审再开
    'review_device_login_as_vip': False,
    'oauth_login_enabled': True,
    'session_ttl_days': 7,
    'session_remember_days': 30,
    'iap_verify_enabled': True,
    'iap_restore_enabled': True,
    'iap_sandbox_allowed': True,
    'iap_unverified_allowed': False,
}

# ---------------------------------------------------------------------------
# 全局默认配置（空 language + 空 platform 种子行）
# ensure_default_configs 只补缺失 key，不覆盖运营已改值
# ---------------------------------------------------------------------------
DEFAULTS = {
    # —— 品牌 / 法务链接（空 url_* 时 App 可回落到本机 /legal/...）——
    'brand_name': 'Selah',
    'legal_entity_name': '[Pending legal entity name]',
    'url_privacy': '',  # 空则 app-config 回落到本机 /legal/privacy/
    'url_terms': '',
    'url_help': '',
    'url_download': '',
    'url_subscription': '',  # 空则回落本机 /legal/subscription/
    'url_instagram': 'https://instagram.com/selah',
    'url_tiktok': 'https://www.tiktok.com/@selah',
    'url_facebook': 'https://facebook.com/selah',
    'support_email': 'support@selah.app',
    # VIP 权益文案（App membership/plans 读取；{vip_daily_quota} 运行时替换）
    'vip_benefits': [
        'Unlock VIP topics & wallpapers',
        'Hide watermark on share cards',
        '{vip_daily_quota} AI messages / day',
    ],
    # VIP 页主 CTA（试用价文案；运营可改）
    'vip_cta_text': 'Try for $0.00',
    # AI 空态欢迎气泡（language×platform 可覆写）
    'ai_greeting': (
        "Hi! I'm here for you. Go ahead and share how you're feeling or what you need today."
    ),
    # 开发旁路：非 DEBUG 生产默认拒绝 sandbox 发权；运营台可显式打开（仍建议仅预发）
    'iap_sandbox_allowed': True,
    'iap_verify_enabled': True,
    'iap_restore_enabled': True,
    'iap_unverified_allowed': False,
    'device_login_enabled': True,
    # 提审临时：快捷登录默认 VIP；测支付期间关，过审/提审再开（运营台也可改）
    'review_device_login_as_vip': False,
    'oauth_login_enabled': True,
    'session_ttl_days': 7,
    'session_remember_days': 30,
    'translate_enabled': True,
    'bible_listen_bg_url': '/static/ui/bible/listen/bg-listen.png',
    'bible_audio_root': '',
    'manna_quiz_hub_bg_url': '',
    'manna_quiz_hub_bg_night_url': '',
    # 法务正文覆写（空则用 legal_views 内置草稿；正式上架优先填 url_* 外链）
    'legal_body_privacy': '',
    'legal_body_terms': '',
    'legal_body_subscription': '',
    'legal_body_help': '',
    'legal_body_download': '',

    # —— 免费额度账本 ——
    'newcomer_bonus': 3,          # 新人一次性赠送次数（grant_newcomer_bonus）
    'checkin_reward': 1,          # 签到奖励次数（走 add_free_quota）
    'free_quota_cap': 10,         # free_quota 上限；加分/新人礼都受此 cap

    # —— VIP 日额度 ——
    'vip_daily_quota': 100,       # 本地日切后重置 vip_daily_remaining 的目标值

    # —— 冷启动推荐比例（与 selah_recommend 共用）——
    'cold_start_interest_ratio': 70,  # 有画像时兴趣池占比
    'cold_start_ops_ratio': 30,       # 运营冷启动池占比
    'expose_dedupe_window': 100,      # 最近 N 条曝光不重复进 Feed
    'checkin_auto_popup': True,
    'newcomer_like_goal': 5,          # 新人喜欢引导目标次数
    'like_soft_signal': True,  # 新人喜欢完成后，喜欢语录标签并入兴趣池
    # 新人引导文案（空=App 用 i18n；按 language×platform 可覆写）
    'newcomer_intro_title': '',
    'newcomer_intro_body': '',
    'newcomer_done_text': '',
    'newcomer_congrats_text': '',

    # —— 连续打卡爱心 ——
    'streak_miss_reset_days': 3,      # 连续缺勤超过 N 天清零 streak
    'streak_heart_initial': 3,
    'streak_heart_max': 5,
    'streak_heart_refill_every': 7,  # 连续打满 N 天奖励 1 颗爱心
    'streak_heart_refill_amount': 1,

    # —— AI 模型（默认 Gemini；可用 AI_API_BASE / AI_API_KEY / AI_MODEL 覆盖）——
    'ai_provider': 'openai_compatible',
    'ai_model': 'gemini-3.8-flash',
    'ai_temperature': 0.7,
    'ai_timeout': 60,
    'ai_system_prompt': (
        'You are Selah, a gentle spiritual companion. Offer prayerful encouragement. '
        'Do not give medical prescriptions, doctrinal condemnation, hate, or self-harm guidance. '
        'If the user is in crisis, encourage seeking local help.'
    ),

    # —— IAP 商店商品 ID（与 membership_products.CONFIG_KEY_BY_SLOT 对齐）——
    'iap_ios_product_id': 'selah.vip.monthly',
    'iap_android_product_id': 'selah.vip.monthly',
    'iap_ios_yearly_product_id': 'selah.vip.yearly',
    'iap_android_yearly_product_id': 'selah.vip.yearly',
    'trial_days': 3,

    # —— 冷启 Welcome 全屏（运营台可关；内容见 WelcomeScreen 表）——
    'welcome_enabled': True,

    # —— Selah 2.0 体验开关 ——
    'selah_2_0_features_enabled': True,  # App 决定是否展示 2.0 问卷题；服务端仍按题 enabled 下发
    'trial_intro_enabled': True,         # 进 VIP 前是否拉 trial_intro 夹层
    # 2.0 teaser 键保留兼容；3.0 以 *_enabled 真能力为准（App 可读任一）
    'widget_teaser_enabled': True,
    'app_icon_teaser_enabled': True,
    'dynamic_wallpaper_tab_enabled': True,

    # —— Selah 3.0 ——
    'selah_3_0_features_enabled': True,
    'compose_enabled': True,
    'quiz_enabled': True,
    'support_selah_enabled': True,
    'url_rate': '',
    'compose_entry_label': 'Create',
    'support_rate_label': 'Rate Selah',
    'support_share_label': 'Share Selah',
    'support_help_label': 'Help & FAQ',
    'widget_enabled': True,
    'app_icon_enabled': True,
    'dynamic_wallpaper_enabled': True,
    'widget_default_theme': 'default',
    'widget_default_border': 'plain',
    'widget_default_refresh': 'daily',
    'widget_vip_borders': [],
    'watch_enabled': True,

    # —— AI 安全：类别开关 + 拒答文案（可运营改）——
    'safety_cat_self_harm_enabled': True,
    'safety_cat_violence_enabled': True,
    'safety_cat_illegal_enabled': True,
    'safety_cat_hate_extremism_enabled': True,
    'safety_cat_religion_dispute_enabled': True,
    'safety_cat_medical_enabled': True,
    'ai_refuse_general': (
        'I care about your safety. I cannot help with that request. '
        'Let us return to gentle prayer, affirmations, or today’s verse.'
    ),
    'ai_refuse_self_harm': (
        'I care about you. I cannot help with self-harm or suicide. '
        'Please contact local emergency services or someone you trust right now.'
    ),
    'ai_refuse_violence': (
        'I cannot help with violence or harming others. '
        'I am here for peace, prayer, and gentle encouragement.'
    ),
    'ai_refuse_illegal': (
        'I cannot help with illegal or dangerous activities. '
        'Let us turn toward hope and healthy choices instead.'
    ),
    'ai_refuse_hate_extremism': (
        'I cannot support hate or extremist violence. '
        'Selah is a place for love, dignity, and peace.'
    ),
    'ai_refuse_religion_dispute': (
        'I will not take sides in harsh religious disputes. '
        'I can walk with you in personal peace, prayer, and kindness.'
    ),
    'ai_refuse_medical': (
        'I cannot give medical prescriptions or diagnoses. '
        'Please talk with a qualified professional; I can offer gentle self-care encouragement.'
    ),
    'ai_crisis_footer': 'If you are in crisis, please reach out using the resources below.',
}


def normalize_lang(lang):
    """
    语言码规范化：取主语言段小写，最长 16；空串表示「全部」。

    例：'zh-CN' → 'zh'；'EN' → 'en'；None/空白 → ''。
    """
    if not lang:
        return ''
    return str(lang).strip().lower().split('-')[0][:16]


def normalize_platform(platform):
    """
    平台规范化：ios/iphone/ipad → ios；android → android；其余视为空（全部）。
    """
    p = (platform or '').strip().lower()
    if p in ('ios', 'iphone', 'ipad'):
        return 'ios'
    if p in ('android',):
        return 'android'
    return ''


def normalize_app_key(app_key):
    """
    应用标识规范化：小写字母数字与 _.- ；空串表示「全部 App」。
    """
    if not app_key:
        return ''
    s = str(app_key).strip().lower()[:32]
    # 仅保留安全字符，避免脏 header
    out = ''.join(ch for ch in s if ch.isalnum() or ch in '._-')
    return out


def set_config_context(language='', platform='', app_key=''):
    """写入请求级配置上下文，供后续 get_config / get_configs 默认回退使用。"""
    _cfg_app_key.set(normalize_app_key(app_key))
    _cfg_language.set(normalize_lang(language))
    _cfg_platform.set(normalize_platform(platform))
    _cfg_degraded.set(False)


def get_config_context():
    """返回当前请求级 (app_key, language, platform)。"""
    return _cfg_app_key.get(), _cfg_language.get(), _cfg_platform.get()


def clear_config_context():
    """清空请求级 app/语言/平台上下文。"""
    _cfg_app_key.set('')
    _cfg_language.set('')
    _cfg_platform.set('')
    _cfg_degraded.set(False)


def set_config_context_from_user(user):
    """
    按用户 app_key / system_lang / platform 设置配置上下文；user 为空则清空。
    """
    if not user:
        clear_config_context()
        return
    set_config_context(
        getattr(user, 'system_lang', '') or '',
        getattr(user, 'platform', '') or '',
        getattr(user, 'app_key', '') or '',
    )


def _request_query_get(request, key, default=None):
    """兼容 DRF Request.query_params 与 Django HttpRequest.GET。"""
    qp = getattr(request, 'query_params', None)
    if qp is not None:
        return qp.get(key, default)
    get = getattr(request, 'GET', None)
    if get is not None:
        return get.get(key, default)
    return default


def set_config_context_from_request(request, *, prefer_user=True):
    """
    从 HTTP 请求解析作用域：header/query 优先于用户字段（登录后可补齐）。

    Headers: X-App-Key / app-key；Accept-Language；X-Platform / platform query。
    兼容 DRF 与 Django 原生 request。
    """
    headers = getattr(request, 'headers', None) or {}
    app = (
        headers.get('X-App-Key')
        or headers.get('app-key')
        or _request_query_get(request, 'app_key')
        or ''
    )
    lang = (
        _request_query_get(request, 'language')
        or (headers.get('Accept-Language') or '').split(',')[0]
        or ''
    )
    plat = (
        _request_query_get(request, 'platform')
        or headers.get('X-Platform')
        or headers.get('platform')
        or ''
    )
    user = getattr(request, 'user', None)
    if prefer_user and user is not None and getattr(user, 'is_authenticated', False):
        if not app:
            app = getattr(user, 'app_key', '') or ''
        q_lang = _request_query_get(request, 'language')
        if not (q_lang or '').strip():
            lang = getattr(user, 'system_lang', '') or lang
        if not plat:
            plat = getattr(user, 'platform', '') or ''
    set_config_context(lang, plat, app)


def _fallback_scopes(app_key='', language='', platform=''):
    """
    生成 app×language×platform 八级回退顺序（去重保序）。
    """
    app = normalize_app_key(app_key)
    lang = normalize_lang(language)
    plat = normalize_platform(platform)
    seen = []
    for triple in (
        (app, lang, plat),
        (app, lang, ''),
        (app, '', plat),
        (app, '', ''),
        ('', lang, plat),
        ('', lang, ''),
        ('', '', plat),
        ('', '', ''),
    ):
        if triple not in seen:
            seen.append(triple)
    return seen


def get_config(key, default=None, language=None, platform=None, app_key=None):
    """
    读取单条运营配置。

    参数为 None：用 ContextVar；显式传入则覆盖。
    同维多行硬规则：取 priority 更大者（同分取 id 更大）。
    """
    if default is None:
        default = DEFAULTS.get(key)
    app = _cfg_app_key.get() if app_key is None else app_key
    lang = _cfg_language.get() if language is None else language
    plat = _cfg_platform.get() if platform is None else platform
    try:
        for A, L, P in _fallback_scopes(app, lang, plat):
            row = (
                AppConfig.objects
                .filter(key=key, app_key=A, language=L, platform=P)
                .order_by('-priority', '-id')
                .first()
            )
            if row is not None:
                return row.value
    except Exception:
        logger.exception('get_config failed key=%s', key)
        _cfg_degraded.set(True)
    return default


def config_as_bool(key, default=True, language=None, platform=None, app_key=None):
    """把运营配置解析成 bool（兼容 JSON true/false、1/0、yes/no）。"""
    v = get_config(key, default, language=language, platform=platform, app_key=app_key)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v or '').strip().lower()
    if s in ('0', 'false', 'no', 'off', 'none', 'null'):
        return False
    if s in ('1', 'true', 'yes', 'on'):
        return True
    return bool(default)


def session_ttl_seconds(remember=False):
    """登录 Redis/Cookie TTL（秒）。运营可配 session_ttl_days / session_remember_days，范围 1–365。"""
    default_days = 30 if remember else 7
    key = 'session_remember_days' if remember else 'session_ttl_days'
    try:
        days = int(get_config(key, default_days) or default_days)
    except (TypeError, ValueError):
        days = default_days
    days = max(1, min(365, days))
    return int(timedelta(days=days).total_seconds())


def consume_config_degraded():
    """app-config 用：读失败后仍回 DEFAULTS 时置 true，便于 QA 发现配错。读完复位，避免串请求。"""
    try:
        flag = bool(_cfg_degraded.get())
    except LookupError:
        flag = False
    _cfg_degraded.set(False)
    return flag


def get_configs(keys, language=None, platform=None, app_key=None):
    """一次查出多个 key（按 app×language×platform 回退；同维取 priority 最大）。"""
    keys = list(keys or [])
    if not keys:
        return {}
    app = _cfg_app_key.get() if app_key is None else app_key
    lang = _cfg_language.get() if language is None else language
    plat = _cfg_platform.get() if platform is None else platform
    scopes = _fallback_scopes(app, lang, plat)
    apps = {A for A, _, _ in scopes}
    langs = {L for _, L, _ in scopes}
    plats = {P for _, _, P in scopes}
    # (key,A,L,P) -> (priority, id, value)；同维保留 priority/id 更大者
    lookup = {}
    try:
        for row in AppConfig.objects.filter(
            key__in=keys, app_key__in=apps, language__in=langs, platform__in=plats,
        ):
            slot = (row.key, row.app_key or '', row.language or '', row.platform or '')
            pri = int(getattr(row, 'priority', 0) or 0)
            rid = int(getattr(row, 'id', 0) or 0)
            prev = lookup.get(slot)
            if prev is None or pri > prev[0] or (pri == prev[0] and rid > prev[1]):
                lookup[slot] = (pri, rid, row.value)
    except Exception:
        logger.exception('get_configs failed keys=%s', keys)
        _cfg_degraded.set(True)
    out = {}
    for key in keys:
        found = False
        for A, L, P in scopes:
            hit = lookup.get((key, A, L, P))
            if hit is not None:
                out[key] = hit[2]
                found = True
                break
        if not found:
            out[key] = DEFAULTS.get(key)
    return out


def set_config(key, value, remark='', language='', platform='', app_key='', priority=0):
    """
    写入/更新指定 scope+priority 的配置行；返回 AppConfig 实例。
    空 app_key/language/platform 写入即「全局」行。
    priority 默认 0；同维不同 priority 可并存，单读取更大者。
    """
    app = normalize_app_key(app_key)
    lang = normalize_lang(language)
    plat = normalize_platform(platform)
    pri = int(priority or 0)
    row, _ = AppConfig.objects.update_or_create(
        key=key,
        app_key=app,
        language=lang,
        platform=plat,
        priority=pri,
        defaults={'value': value, 'remark': remark},
    )
    return row


_defaults_ensured = False


def ensure_default_configs():
    """
    种子：为全局 app_key+language+platform 补齐 DEFAULTS 中缺失的 key。

    进程内只跑一次（开发热重载会重置）。避免每次 device-login 对远程 PG
    打几十次 exists()，拖到数秒级。
    """
    global _defaults_ensured
    if _defaults_ensured:
        return
    for k, v in DEFAULTS.items():
        if not AppConfig.objects.filter(key=k, app_key='', language='', platform='', priority=0).exists():
            AppConfig.objects.create(
                key=k, value=v, app_key='', language='', platform='', priority=0, remark='seed',
            )
    # 登录送 VIP：全局种子行与 DEFAULTS 对齐（测支付关；提审再改 DEFAULTS 即可）
    AppConfig.objects.filter(
        key='review_device_login_as_vip', app_key='', language='', platform='', priority=0,
    ).exclude(value=DEFAULTS['review_device_login_as_vip']).update(
        value=DEFAULTS['review_device_login_as_vip'],
    )
    repair_vip_benefits_placeholder()
    ensure_manna_defaults()
    _defaults_ensured = True


_manna_defaults_ensured = False


def ensure_manna_defaults():
    """只补 app_key=manna 缺失键，不覆盖运营已改值，不写全局行。"""
    global _manna_defaults_ensured
    if _manna_defaults_ensured:
        return
    for k, v in MANNA_DEFAULTS.items():
        AppConfig.objects.get_or_create(
            key=k, app_key=MANNA_APP_KEY, language='', platform='', priority=0,
            defaults={'value': v, 'remark': 'manna-seed'},
        )
    # 种子包（remark=manna-seed）对齐冻结默认；运营改过 remark/priority 的包不动
    for k in ('manna_prayer_amen_reward', 'manna_devotion_amen_reward'):
        AppConfig.objects.filter(
            key=k, app_key=MANNA_APP_KEY, language='', platform='', priority=0,
            remark='manna-seed',
        ).exclude(value=MANNA_DEFAULTS[k]).update(value=MANNA_DEFAULTS[k])
    # 登录送 VIP：manna 种子行与 MANNA_DEFAULTS 对齐
    AppConfig.objects.filter(
        key='review_device_login_as_vip', app_key=MANNA_APP_KEY, language='', platform='', priority=0,
    ).exclude(value=MANNA_DEFAULTS['review_device_login_as_vip']).update(
        value=MANNA_DEFAULTS['review_device_login_as_vip'],
    )
    _manna_defaults_ensured = True


def get_manna_wallet_snapshot(user, app_key=None):
    """读取 Manna 钱包快照；无行则 balance=0 / stats={}（Phase 0 占位）。"""
    from models.models import MannaWallet

    app = normalize_app_key(app_key if app_key is not None else (getattr(user, 'app_key', '') or ''))
    if not app:
        app = MANNA_APP_KEY
    row = MannaWallet.objects.filter(user_id=user.id, app_key=app).first()
    if not row:
        return {'balance': 0, 'stats': {}}
    stats = row.stats if isinstance(row.stats, dict) else {}
    return {'balance': int(row.balance or 0), 'stats': stats}


def repair_vip_benefits_placeholder():
    """把各 scope 下 vip_benefits 中写死的日额度文案改为 {vip_daily_quota} 占位符。"""
    import re
    hard_exact = '100 AI messages / day'
    placeholder = '{vip_daily_quota} AI messages / day'
    # 匹配 "N AI messages / day"（N 为数字，非占位符）
    numeric_pat = re.compile(r'^\d+\s+AI messages / day$')
    for row in AppConfig.objects.filter(key='vip_benefits'):
        val = row.value
        if not isinstance(val, list):
            continue
        new_list = []
        changed = False
        for item in val:
            s = str(item)
            if '{vip_daily_quota}' in s:
                new_list.append(s)
            elif s == hard_exact or numeric_pat.match(s):
                new_list.append(placeholder)
                changed = True
            else:
                new_list.append(s)
        if changed:
            row.value = new_list
            row.save(update_fields=['value'])

def ensure_default_welcome_screens():
    """缺专属 Welcome 时按 App 写入默认文案（不覆盖运营已有行；不再写空维）。"""
    from models.models import WelcomeScreen
    seeds = (
        ('selah', 'Selah', 'Grow closer to God'),
        ('manna', 'Manna', 'Daily bread for the journey'),
    )
    for app, title, subtitle in seeds:
        if WelcomeScreen.objects.filter(app_key=app).exists():
            continue
        WelcomeScreen.objects.create(
            title=title,
            subtitle=subtitle,
            cta_text='Continue',
            image_url='',
            version=1,
            sort_order=0,
            enabled=True,
            app_key=app,
            language='',
            platform='',
        )


def scope_score(obj, lang, platform, app_key=None):
    """
    内容行与目标 app×language×platform 的匹配得分；不匹配返回 -1。

    规则：
    - app_key 必须与目标 App 一致（空维内容不再匹配具体 App）
    - 语言/端为空 = 可回退匹配；有具体值且与目标不一致 → -1
    - app 命中 +4，语言 +2，平台 +1
    - app_key=None 时读请求级 ContextVar
    """
    app = normalize_app_key(_cfg_app_key.get() if app_key is None else app_key)
    lang = normalize_lang(lang)
    plat = normalize_platform(platform)
    oa = normalize_app_key(getattr(obj, 'app_key', '') or '')
    ol = normalize_lang(getattr(obj, 'language', '') or '')
    op = normalize_platform(getattr(obj, 'platform', '') or '')
    if oa != app:
        return -1
    if ol and lang and ol != lang:
        return -1
    if op and plat and op != plat:
        return -1
    score = 0
    if oa and oa == app:
        score += 4
    if ol and ol == lang:
        score += 2
    if op and op == plat:
        score += 1
    return score


def _row_priority(row):
    """单读冲突裁决：priority 数字大优先；无字段视为 0。"""
    return int(getattr(row, 'priority', 0) or 0)


def filter_scoped_rows(rows, lang, platform, group_key=None, app_key=None):
    """
    从候选行中按 app×language×platform 回退挑选。

    - group_key 为 None：返回所有 score>=0，按 (-score, -priority, sort_order, id) 排序
      （单读场景取 [0] 即作用域分最高且 priority 更大者）
    - group_key 为属性名：同组只保留最优一行（先 score，再 priority）
    - app_key=None 时读请求级 ContextVar（与 get_config 一致）
    """
    scored = []
    for row in rows:
        s = scope_score(row, lang, platform, app_key=app_key)
        if s >= 0:
            scored.append((s, row))
    if not group_key:
        scored.sort(key=lambda x: (
            -x[0],
            -_row_priority(x[1]),
            getattr(x[1], 'sort_order', 0),
            getattr(x[1], 'id', 0),
        ))
        return [r for _, r in scored]
    # 分组取最优：先更高 score，同分取更高 priority，再同分保留先出现的
    best = {}
    for s, row in scored:
        gk = getattr(row, group_key, None)
        prev = best.get(gk)
        if prev is None:
            best[gk] = (s, row)
            continue
        ps, prow = prev
        if s > ps or (s == ps and _row_priority(row) > _row_priority(prow)):
            best[gk] = (s, row)
    out = [r for _, r in best.values()]
    out.sort(key=lambda r: (-_row_priority(r), getattr(r, 'sort_order', 0), getattr(r, 'id', 0)))
    return out


# ---------------------------------------------------------------------------
# 额度账本：免费 free_quota（有 cap） / VIP vip_daily_remaining（按本地日切）
# ---------------------------------------------------------------------------

def user_local_today(user: User):
    """
    用户时区下的「今天」日期；时区非法时回退 UTC。

    VIP 日额度刷新、签到「是否今日已打」等都应以此为准，而非服务器本地日。
    """
    try:
        tz = ZoneInfo(user.timezone or 'UTC')
    except Exception:
        # ZoneInfo 不认的字符串（脏数据）→ UTC，避免刷新逻辑崩溃
        tz = ZoneInfo('UTC')
    return datetime.now(tz).date()


def refresh_vip_daily_if_needed(user: User):
    """
    VIP 日额度按用户本地 0 点刷新；非 VIP 原样返回。

    条件：is_vip_active 且 vip_daily_reset_date != 用户本地今天。
    动作：vip_daily_remaining = vip_daily_quota 配置值；更新 reset_date。

    注意：未跨日则不写库；调用方传入的 user 可能被 save 刷新字段。
    """
    if not user.is_vip_active:
        return user
    today = user_local_today(user)
    if user.vip_daily_reset_date != today:
        user.vip_daily_remaining = int(get_config('vip_daily_quota', 100))
        user.vip_daily_reset_date = today
        user.save(update_fields=['vip_daily_remaining', 'vip_daily_reset_date', 'updated_at'])
    return user


def expire_vip_if_needed(user: User):
    """
    VIP/Plus 过期则降为免费并清除试用标记。

    仅当 vip_level 为 VIP/PLUS 且 vip_expire_at 已过期才降级；
    免费用户不触碰。降级后下次额度走 free_quota 账本。
    """
    if user.vip_level in (User.VIP_VIP, User.VIP_PLUS) and user.vip_expire_at:
        if user.vip_expire_at < dj_tz.now():
            user.vip_level = User.VIP_FREE
            user.is_trial = False
            user.save(update_fields=['vip_level', 'is_trial', 'updated_at'])
    return user


def grant_newcomer_bonus(user: User):
    """
    发放新人免费额度（一次性）。

    规则：
    - newcomer_bonus_granted 已为 True → 不再发，返回 bonus=0
    - 否则 free_quota = min(cap, free_quota + newcomer_bonus)，并置 granted=True
    - 受 free_quota_cap 封顶：即使 bonus 配置很大，也不会超过 cap

    返回 (user, bonus)；bonus 为本次实际意图赠送配置值（已发放标记后），
    若因已发放跳过则为 0。
    """
    if user.newcomer_bonus_granted:
        return user, 0
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if locked.newcomer_bonus_granted:
            return locked, 0
        bonus = int(get_config('newcomer_bonus', 3))
        cap = int(get_config('free_quota_cap', 10))
        locked.free_quota = min(cap, locked.free_quota + bonus)
        locked.newcomer_bonus_granted = True
        locked.save(update_fields=['free_quota', 'newcomer_bonus_granted', 'updated_at'])
        return locked, bonus


def get_remaining_quota(user: User):
    """
    当前可用额度快照。

    副作用：先 expire_vip_if_needed；若仍是 VIP 再 refresh_vip_daily_if_needed。

    返回字典字段：
    - ledger: 'vip_daily_remaining' | 'free_quota'（当前扣减走哪本账）
    - remaining: 当前可消费次数
    - vip_level / is_trial / free_quota：状态镜像
    - cap: 仅免费账本附带，便于前端展示「还可攒到多少」
    """
    user = expire_vip_if_needed(user)
    if user.is_vip_active:
        user = refresh_vip_daily_if_needed(user)
        return {
            'ledger': 'vip_daily_remaining',
            'remaining': user.vip_daily_remaining,
            'vip_level': user.vip_level,
            'is_trial': user.is_trial,
            'free_quota': user.free_quota,  # VIP 期间免费仓仍保留，降级后可用
        }
    return {
        'ledger': 'free_quota',
        'remaining': user.free_quota,
        'vip_level': User.VIP_FREE,
        'is_trial': False,
        'free_quota': user.free_quota,
        'cap': int(get_config('free_quota_cap', 10)),
    }


def can_consume_ai(user: User):
    """是否可扣 1 次 AI；返回 (ok, quota_info)。不改库。"""
    info = get_remaining_quota(user)
    return info['remaining'] > 0, info


def consume_ai_quota(user: User):
    """
    扣减 1 次 AI 额度（debit）。

    路径：
    1. can_consume_ai 预检；不足则不改账本，返回 (False, info)
    2. 重新 get(pk) 降低脏读风险
    3. VIP：再 refresh 日额度；vip_daily_remaining -= 1
    4. 免费：free_quota -= 1；为 0 则失败

    竞态：行锁后再判余额，避免超扣。
    返回 (success, quota_info)；不足或不成功时不改账本。
    """
    ok, info = can_consume_ai(user)
    if not ok:
        return False, info
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if locked.is_vip_active:
            locked = refresh_vip_daily_if_needed(locked)
            if locked.vip_daily_remaining <= 0:
                return False, get_remaining_quota(locked)
            locked.vip_daily_remaining -= 1
            locked.save(update_fields=['vip_daily_remaining', 'updated_at'])
        else:
            if locked.free_quota <= 0:
                return False, get_remaining_quota(locked)
            locked.free_quota -= 1
            locked.save(update_fields=['free_quota', 'updated_at'])
        return True, get_remaining_quota(locked)


def add_free_quota(user: User, amount: int):
    """
    签到等场景加免费次数（credit）；仅动 free_quota，不动 VIP 日额度。

    规则：
    - amount <= 0 → (0, False, user) 无操作
    - 已达 cap → (0, True, user) 表示撞顶，granted=0
    - 否则 free_quota = min(cap, free_quota + amount)；
      granted 为实际增加量（可能小于 amount，因撞 cap）

    返回 (granted, hit_cap, user)。
    """
    if amount <= 0:
        return 0, False, user
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        cap = int(get_config('free_quota_cap', 10))
        if locked.free_quota >= cap:
            return 0, True, locked
        before = locked.free_quota
        locked.free_quota = min(cap, locked.free_quota + amount)
        granted = locked.free_quota - before
        hit_cap = locked.free_quota >= cap
        locked.save(update_fields=['free_quota', 'updated_at'])
        return granted, hit_cap, locked


def user_public_dict(user: User):
    """
    App 侧用户公开资料 + 额度快照（不含密码、token、三方原始凭证等敏感字段）。

    字段含义：
    - user_id / username / device_id：身份与设备游客标识
    - google_bound / apple_bound：是否已绑三方（布尔，非原始 id）
    - login_type：当前登录方式枚举
    - vip_level / is_trial / vip_expire_at：会员态；quota 为 get_remaining_quota 快照
    - onboarding_* / faith_type / prayer_frequency / interest_tags：画像与引导进度
    - display_name / gender / age_range：基础资料（basic_info 题）
    - timezone / register_country / system_lang / platform / app_key：本地化与分发维度
    - bind_prompt_dismissed / marketing_opt_in：弱提示与营销同意
    - sound_enabled / muted / wallpaper_id：客户端偏好
    - checkin_streak / streak_hearts / checkin_*：打卡与爱心
    - newcomer_like_completed：新人喜欢引导是否完成（影响推荐 soft signal）
    - role：user / operator / admin / super_admin（App 通常为 user）
    """
    q = get_remaining_quota(user)
    data = {
        'user_id': user.id,
        'username': user.username,
        'device_id': user.device_id,
        'google_bound': bool(user.google_id),
        'apple_bound': bool(user.apple_id),
        'login_type': user.login_type,
        'vip_level': q['vip_level'],
        'is_trial': q['is_trial'],
        'vip_expire_at': user.vip_expire_at.isoformat() if user.vip_expire_at else None,
        'quota': q,  # 含 ledger / remaining / 可选 cap
        'onboarding_completed': user.onboarding_completed,
        'onboarding_skipped': user.onboarding_skipped,
        'faith_type': user.faith_type,
        'prayer_frequency': user.prayer_frequency,
        'interest_tags': user.interest_tags or [],
        'display_name': user.display_name,
        'gender': user.gender,
        'age_range': user.age_range,
        'timezone': user.timezone,
        'register_country': user.register_country,
        'system_lang': user.system_lang,
        'platform': user.platform or '',
        'app_key': getattr(user, 'app_key', '') or '',
        'bind_prompt_dismissed': user.bind_prompt_dismissed,
        'marketing_opt_in': user.marketing_opt_in,
        'sound_enabled': user.sound_enabled,
        'muted': user.muted,
        'wallpaper_id': user.wallpaper_id,
        'checkin_streak': user.checkin_streak,
        'streak_hearts': user.streak_hearts,
        'checkin_record_enabled': user.checkin_record_enabled,
        'checkin_remind_enabled': user.checkin_remind_enabled,
        'newcomer_like_completed': user.newcomer_like_completed,
        'profile_answers': getattr(user, 'profile_answers', None) or {},
        'reminder_prefs': normalize_reminder_prefs(user),
        'widget_prefs': getattr(user, 'widget_prefs', None) or {},
        'selected_icon_key': getattr(user, 'selected_icon_key', None) or '',
        'alarm_prefs': getattr(user, 'alarm_prefs', None) or {},
        'prayer_lock_prefs': getattr(user, 'prayer_lock_prefs', None) or {},
        'role': user.role,
    }
    from tools.account_deletion import deletion_pending_fields
    data.update(deletion_pending_fields(user))
    # Manna 上下文：附带货币余额与四维统计占位（≠ Selah AI quota）
    ctx_app, _, _ = get_config_context()
    user_app = normalize_app_key(getattr(user, 'app_key', '') or '')
    effective_app = normalize_app_key(ctx_app) or user_app
    if effective_app == MANNA_APP_KEY or user_app == MANNA_APP_KEY:
        snap = get_manna_wallet_snapshot(user, app_key=MANNA_APP_KEY)
        data['manna_balance'] = snap['balance']
        data['manna_stats'] = snap['stats']
    return data


REMINDER_BOOL_KEYS = ('record_enabled', 'streak_remind_enabled', 'checkin_remind_enabled')
# Reminders 页可调钟点的条目；与前端卡片 key 对齐
REMINDER_TIME_KEYS = ('writing', 'practice', 'quotes', 'checkin', 'streak')
DEFAULT_REMINDER_TIMES = {
    'writing': (9, 0),
    'practice': (12, 0),
    'quotes': (12, 0),
    'checkin': (15, 0),
    'streak': (20, 0),
}


def default_reminder_prefs():
    """提醒中心默认三开关。"""
    return {
        'record_enabled': True,
        'streak_remind_enabled': True,
        'checkin_remind_enabled': True,
    }


def _clock_slot(hour, minute):
    """规范化 hour/minute，非法则 None。"""
    try:
        h = max(0, min(23, int(hour)))
        m = max(0, min(59, int(minute)))
    except (TypeError, ValueError):
        return None
    return {'hour': h, 'minute': m, 'time': f'{h:02d}:{m:02d}'}


def default_reminder_times():
    """提醒钟点默认值（本地墙钟，24h）。"""
    return {
        key: _clock_slot(hour, minute)
        for key, (hour, minute) in DEFAULT_REMINDER_TIMES.items()
    }


def _parse_clock(raw):
    """接受 {hour, minute}、{time:'HH:mm'} 或 'HH:mm'。"""
    if isinstance(raw, dict):
        if raw.get('hour') is not None and raw.get('minute') is not None:
            slot = _clock_slot(raw.get('hour'), raw.get('minute'))
            if slot:
                return slot
        t = str(raw.get('time') or '').strip()
        if t:
            return _parse_clock(t)
        return None
    text = str(raw or '').strip()
    if not text or ':' not in text:
        return None
    hh, mm = text.split(':', 1)
    mm = mm.split(':', 1)[0]
    return _clock_slot(hh, mm)


def normalize_reminder_times(stored=None, incoming=None):
    """合并提醒钟点；incoming 可 partial（单 key 或 times{}）。"""
    times = default_reminder_times()

    def _apply(blob):
        if not isinstance(blob, dict):
            return
        src = blob.get('times') if isinstance(blob.get('times'), dict) else blob
        if not isinstance(src, dict):
            return
        for key in REMINDER_TIME_KEYS:
            if key not in src:
                continue
            parsed = _parse_clock(src.get(key))
            if parsed:
                times[key] = parsed

    _apply(stored)
    _apply(incoming)
    return times


def normalize_reminder_prefs(user, incoming=None):
    """规范化 reminder_prefs，并与 checkin_* 列保持同步语义。

    - 关 record → 强制关 streak / checkin 提醒
    - 开提醒但 record 关 → 不允许（调用方应 400）；此处归一时压回 False
    - incoming 为 partial dict；缺省从 user.reminder_prefs / 列字段回填
    - times 与开关分离，拨开关不会清钟点
    """
    base = default_reminder_prefs()
    stored = getattr(user, 'reminder_prefs', None) or {}
    if isinstance(stored, dict):
        for k in REMINDER_BOOL_KEYS:
            if k in stored:
                base[k] = bool(stored[k])
    # 列字段优先兜底（旧客户端只写 checkin_*）
    if getattr(user, 'checkin_record_enabled', None) is not None:
        base['record_enabled'] = bool(user.checkin_record_enabled)
    if getattr(user, 'checkin_remind_enabled', None) is not None:
        base['checkin_remind_enabled'] = bool(user.checkin_remind_enabled)
    if isinstance(incoming, dict):
        for k in REMINDER_BOOL_KEYS:
            if k in incoming:
                base[k] = bool(incoming[k])
    if not base['record_enabled']:
        base['streak_remind_enabled'] = False
        base['checkin_remind_enabled'] = False
    base['times'] = normalize_reminder_times(
        stored if isinstance(stored, dict) else None,
        incoming if isinstance(incoming, dict) else None,
    )
    return base


def apply_reminder_prefs(user, prefs: dict):
    """把规范化 prefs 写回 JSON + checkin_* 列；返回 update_fields。"""
    user.reminder_prefs = prefs
    user.checkin_record_enabled = bool(prefs.get('record_enabled', True))
    user.checkin_remind_enabled = bool(prefs.get('checkin_remind_enabled', True))
    return ['reminder_prefs', 'checkin_record_enabled', 'checkin_remind_enabled', 'updated_at']
