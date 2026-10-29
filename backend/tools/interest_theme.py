#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""兴趣主题归一化：Onboarding interest_tags 与语录打标共用。

业务说明:
    运营/语录用 selah_enums.INTEREST_TAGS（prayer/peace/…）。
    App 引导题 value 常为展示文案（Strength、Anxiety Relief），须别名映射后再入库。
    无法映射的合法主题保留为 slug，避免 complete 400。
"""
from tools.selah_enums import INTEREST_TAGS, values_of

# 枚举 value + label（大小写不敏感）→ 规范 value
_CANONICAL = {}
for _value, _label in INTEREST_TAGS:
    _CANONICAL[_value.lower()] = _value
    _CANONICAL[_label.lower()] = _value

# App bootstrap / 历史展示值 → 规范 value；无对应枚举则自留 slug
_ALIASES = {
    'anxiety relief': 'anxiety',
    'anxiety_relief': 'anxiety',
    'work & purpose': 'work',
    'work and purpose': 'work',
    'work_purpose': 'work',
    'self-worth': 'self_worth',
    'self worth': 'self_worth',
    'self_worth': 'self_worth',
    'faith': 'faith',
    'strength': 'strength',
    'love': 'love',
    'guidance': 'guidance',
    'growth': 'growth',
}

# 引导题额外主题（不在 INTEREST_TAGS，但仍允许入库）
_EXTRA_ALLOWED = frozenset({
    'faith', 'strength', 'love', 'guidance', 'growth', 'self_worth',
})

ALLOWED_INTERESTS = frozenset(values_of(INTEREST_TAGS)) | _EXTRA_ALLOWED


def _slug(value):
    s = str(value or '').strip().lower().replace('&', ' and ')
    s = s.replace('-', '_').replace('/', '_')
    return '_'.join(part for part in s.replace(' ', '_').split('_') if part)


def normalize_interest_value(value):
    """单个兴趣值 → 规范 slug；无法识别返回空串。"""
    raw = str(value or '').strip()
    if not raw:
        return ''
    key = raw.lower()
    if key in _CANONICAL:
        return _CANONICAL[key]
    if key in _ALIASES:
        return _ALIASES[key]
    slug = _slug(raw)
    if slug in _CANONICAL:
        return _CANONICAL[slug]
    if slug in _ALIASES:
        return _ALIASES[slug]
    if slug in ALLOWED_INTERESTS:
        return slug
    return ''


def is_valid_interest_value(value):
    """单个兴趣值是否可归一（空串视为无效）。"""
    return bool(normalize_interest_value(value))


def normalize_interest_list(values):
    """列表归一：去空、映射别名、去重保序。无法识别的项丢弃。"""
    ok = []
    for raw in values or []:
        v = normalize_interest_value(raw)
        if v and v not in ok:
            ok.append(v)
    return ok


def sync_interest_tags_from_theme(theme, existing=None):
    """把主主题并入 interest_tags（去重保序）；无法归一则原样保留。"""
    tags = list(existing or [])
    raw = str(theme or '').strip()
    if not raw:
        return tags
    v = normalize_interest_value(raw) or raw
    if v not in tags:
        tags.append(v)
    return tags
