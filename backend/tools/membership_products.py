#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
会员商品：商店 product_id ↔ App plans / 验单。

DEFAULT_PRODUCTS 种子月/年 × ios/android（默认 app_key=selah）；
CONFIG_KEY_BY_SLOT 与 AppConfig iap_* 对齐。

同步方向：
- ensure_default_products：种 MembershipProduct 行（按 App）
- sync_iap_config_keys：按 app_key 回写对应维 AppConfig iap_*
- list_enabled_products_for_app：只返回当前 App（+ 无专属时回落空维遗留）
- resolve_product：验单按 platform+store_product_id；可选 app_key 优先匹配
"""
import logging

from models.models import MembershipProduct
from tools.selah_quota import get_config, set_config, normalize_app_key, get_config_context

logger = logging.getLogger(__name__)

# (plan_type, platform, store_product_id, title, duration_days, sort_weight)
DEFAULT_PRODUCTS = [
    ('monthly', 'ios', 'selah.vip.monthly', 'Selah VIP Monthly', 30, 20),
    ('monthly', 'android', 'selah.vip.monthly', 'Selah VIP Monthly', 30, 20),
    ('yearly', 'ios', 'selah.vip.yearly', 'Selah VIP Yearly', 365, 10),
    ('yearly', 'android', 'selah.vip.yearly', 'Selah VIP Yearly', 365, 10),
]

CONFIG_KEY_BY_SLOT = {
    ('monthly', 'ios'): 'iap_ios_product_id',
    ('monthly', 'android'): 'iap_android_product_id',
    ('yearly', 'ios'): 'iap_ios_yearly_product_id',
    ('yearly', 'android'): 'iap_android_yearly_product_id',
}


def resolve_price_display(p: MembershipProduct, language: str = None) -> str:
    """按语言取价格文案：i18n[lang] → i18n[en] → price_display。"""
    from tools.selah_quota import normalize_lang
    lang = normalize_lang(language) if language is not None else get_config_context()[1]
    i18n = getattr(p, 'price_display_i18n', None) or {}
    if isinstance(i18n, dict):
        for key in (lang, (lang or '')[:2], 'en'):
            val = str(i18n.get(key) or '').strip()
            if val:
                return val
    return (p.price_display or '').strip()


def serialize_product(p: MembershipProduct, language: str = None):
    """
    MembershipProduct → App/运营台通用字典。

    双字段别名：plan_id≈plan_type、product_id≈store_product_id，兼容旧客户端字段名。
    """
    i18n = getattr(p, 'price_display_i18n', None) or {}
    if not isinstance(i18n, dict):
        i18n = {}
    app = getattr(p, 'app_key', '') or ''
    return {
        'id': p.id,
        'app_key': app,
        'plan_id': p.plan_type,
        'plan_type': p.plan_type,
        'platform': p.platform,
        'product_id': p.store_product_id,
        'store_product_id': p.store_product_id,
        'title': p.title or f'VIP {p.plan_type.title()}',
        'price_display': resolve_price_display(p, language),
        'price_display_default': p.price_display or '',
        'price_display_i18n': i18n,
        'price_cents': int(getattr(p, 'price_cents', 0) or 0),
        'currency': getattr(p, 'currency', None) or 'USD',
        'duration_days': p.duration_days,
        'trial_days': p.trial_days,
        'enabled': p.enabled,
        'sort_weight': p.sort_weight,
        'remark': p.remark or '',
        'created_at': p.created_at.isoformat() if p.created_at else None,
        'updated_at': p.updated_at.isoformat() if p.updated_at else None,
    }


def ensure_default_products(force_sync=False, app_key='selah'):
    """
    从 AppConfig / 默认值种入会员商品（默认仅 selah 维）。仅新建或 force_sync 时回写 iap_*。
    非 selah App 不自动种 Selah 商店 ID，避免串品牌。
    """
    app = normalize_app_key(app_key) or 'selah'
    if MembershipProduct.objects.filter(app_key=app).exists() and not force_sync:
        return 0
    if app not in ('', 'selah'):
        return 0

    created = 0
    for plan_type, platform, default_pid, title, days, weight in DEFAULT_PRODUCTS:
        cfg_key = CONFIG_KEY_BY_SLOT[(plan_type, platform)]
        pid = get_config(cfg_key, language='', platform='', app_key=app) or default_pid
        if isinstance(pid, dict):
            pid = default_pid
        pid = str(pid).strip() or default_pid
        if MembershipProduct.objects.filter(platform=platform, store_product_id=pid).exists():
            continue
        if MembershipProduct.objects.filter(
            app_key=app, platform=platform, plan_type=plan_type, enabled=True,
        ).exists():
            continue
        MembershipProduct.objects.create(
            app_key=app,
            plan_type=plan_type,
            platform=platform,
            store_product_id=pid,
            title=title,
            duration_days=days,
            enabled=True,
            sort_weight=weight,
            remark=f'pack:{app}',
        )
        created += 1
    if created or force_sync:
        logger.info('ensure_default_products app=%s created=%s force_sync=%s', app, created, force_sync)
        sync_iap_config_keys(app_key=app)
    return created


def sync_iap_config_keys(app_key=None):
    """
    按 App 回写 AppConfig iap_*（与文档「按 App 分行」一致）。

    - app_key 显式传入：只同步该维（含 '' 全局）
    - app_key is None：同步库中出现过的全部 app_key 维
    每个 (app, plan_type, platform) 取 enabled 中 sort_weight/id 最大的一行。
    """
    if app_key is not None:
        apps = [normalize_app_key(app_key)]
    else:
        apps = sorted({
            normalize_app_key(a) for a in
            MembershipProduct.objects.values_list('app_key', flat=True)
        })
        if not apps:
            apps = ['']
    for a in apps:
        for (plan_type, platform), key in CONFIG_KEY_BY_SLOT.items():
            row = (
                MembershipProduct.objects
                .filter(app_key=a, plan_type=plan_type, platform=platform, enabled=True)
                .order_by('-sort_weight', '-id')
                .first()
            )
            if not row:
                continue
            current = get_config(key, language='', platform='', app_key=a)
            if current == row.store_product_id:
                continue
            set_config(
                key, row.store_product_id,
                remark='synced from membership product',
                language='', platform='', app_key=a,
            )


def _product_rows_for_app(app):
    """当前 App 启用商品；无专属行时回落空维遗留行（兼容旧数据）。"""
    app = normalize_app_key(app)
    qs = MembershipProduct.objects.filter(enabled=True).order_by(
        '-sort_weight', 'plan_type', 'platform', 'id',
    )
    exact = list(qs.filter(app_key=app))
    if exact or not app:
        return exact
    return list(qs.filter(app_key=''))


def list_enabled_products_for_app(app_key=None):
    """
    App 会员页：仅当前 App 的启用商品（避免双品牌 SKU 串台）。

    app_key=None 时读 ContextVar；trial_days 行上为 None 时回落该 App 的 trial_days 配置。
    """
    if app_key is None:
        app_key = get_config_context()[0]
    app = normalize_app_key(app_key)
    ensure_default_products(app_key=app or 'selah')
    try:
        from tools.selah_virtual_seed import ensure_virtual_prices
        ensure_virtual_prices()
    except Exception:
        pass
    rows = _product_rows_for_app(app)
    trial_cfg = int(get_config('trial_days', 3, language='', platform='', app_key=app))
    out = []
    for p in rows:
        item = serialize_product(p)
        item['trial_days'] = p.trial_days if p.trial_days is not None else trial_cfg
        item['product_id'] = p.store_product_id
        out.append(item)
    return out


def resolve_product(platform: str, product_id: str, app_key=None):
    """
    验单：按平台 + 商店商品 ID 解析套餐。

    1. 优先查 MembershipProduct(platform, store_product_id, enabled)，有 app_key 时优先该维
    2. 无行：用当前 App 的 AppConfig iap_* 映射
    3. 配置也未命中 → None
    """
    if app_key is None:
        app_key = get_config_context()[0]
    app = normalize_app_key(app_key)
    ensure_default_products(app_key=app or 'selah')
    qs = MembershipProduct.objects.filter(
        platform=platform, store_product_id=product_id, enabled=True,
    )
    row = None
    if app:
        row = qs.filter(app_key=app).first()
    if row is None:
        row = qs.filter(app_key='').first()
    if row:
        return row
    expected_map = {
        'ios': {
            get_config('iap_ios_product_id', language='', platform='', app_key=app): ('monthly', 30),
            get_config('iap_ios_yearly_product_id', language='', platform='', app_key=app): ('yearly', 365),
        },
        'android': {
            get_config('iap_android_product_id', language='', platform='', app_key=app): ('monthly', 30),
            get_config('iap_android_yearly_product_id', language='', platform='', app_key=app): ('yearly', 365),
        },
    }
    hit = (expected_map.get(platform) or {}).get(product_id)
    if not hit:
        return None
    plan_type, days = hit

    class _Tmp:
        pass

    t = _Tmp()
    t.id = None
    t.app_key = app
    t.plan_type = plan_type
    t.platform = platform
    t.store_product_id = product_id
    t.duration_days = days
    t.trial_days = None
    t.title = ''
    t.price_display = ''
    t.price_cents = 0
    t.currency = 'USD'
    t.enabled = True
    t.sort_weight = 0
    t.remark = 'from-config'
    return t
