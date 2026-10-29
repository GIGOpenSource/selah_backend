#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""标签库：预置类型/标签、检索键、发布校验、展示标签收录。"""
import re
from django.db.models import Count, Q

from models.models import TagType, Tag, DisplayTag, Quote, Wallpaper, WallpaperUsage

SCOPE_TEXT = TagType.SCOPE_TEXT
SCOPE_WALLPAPER = TagType.SCOPE_WALLPAPER
SCOPE_BOTH = TagType.SCOPE_BOTH

DEFAULT_TYPES = [
    {
        'key': 'content', 'label': 'Content Type', 'prefix': 'C',
        'scope': SCOPE_TEXT, 'required': True, 'locked': True, 'sort_order': 10,
    },
    {
        'key': 'visual', 'label': 'Visual Style', 'prefix': 'V',
        'scope': SCOPE_WALLPAPER, 'required': True, 'locked': True, 'sort_order': 20,
    },
    {
        'key': 'theme', 'label': 'Theme', 'prefix': 'T',
        'scope': SCOPE_BOTH, 'required': True, 'locked': False, 'sort_order': 30,
    },
    {
        'key': 'scenario', 'label': 'Life Scenario', 'prefix': 'S',
        'scope': SCOPE_BOTH, 'required': False, 'locked': False, 'sort_order': 40,
    },
    {
        'key': 'emotion', 'label': 'Emotion', 'prefix': 'E',
        'scope': SCOPE_BOTH, 'required': False, 'locked': False, 'sort_order': 50,
    },
]

# (type_key, name, related_themes)
DEFAULT_TAGS = [
    ('content', 'Bible Verse', []),
    ('content', 'Christian Encouragement', []),
    ('content', 'Prayer', []),
    ('content', 'Christian Quote', []),
    ('content', 'Seasonal Content', []),
    ('theme', 'Peace', []),
    ('theme', 'Hope', []),
    ('theme', 'Faith', []),
    ('theme', 'Strength', []),
    ('theme', 'Love', []),
    ('theme', 'Healing', []),
    ('theme', 'Gratitude', []),
    ('theme', 'Guidance', []),
    ('theme', 'Growth', []),
    ('theme', 'Prayer', []),
    ('theme', 'Self-Worth', []),
    # 兼容旧兴趣枚举（引导/推荐 1:1，避免有损合并）
    ('theme', 'Family', []),
    ('theme', 'Anxiety Relief', []),
    ('theme', 'Courage', []),
    ('theme', 'Work & Purpose', []),
    ('theme', 'Relationships', []),
    ('scenario', 'Work Stress', ['Peace', 'Strength']),
    ('scenario', 'Feeling Down', ['Hope', 'Healing']),
    ('scenario', 'Before Sleep', ['Peace', 'Prayer']),
    ('scenario', 'Morning Start', ['Faith', 'Gratitude']),
    ('scenario', 'Relationship Hurt', ['Love', 'Healing']),
    ('scenario', 'Loneliness', ['Love', 'Hope']),
    ('scenario', 'Uncertainty', ['Guidance', 'Faith']),
    ('scenario', 'Waiting Season', ['Hope', 'Faith']),
    ('scenario', 'New Beginning', ['Hope', 'Growth']),
    ('scenario', 'Family Need', ['Love', 'Prayer']),
    ('scenario', 'Physical Tiredness', ['Strength', 'Peace']),
    ('scenario', 'Fearful Moment', ['Faith', 'Peace']),
    ('scenario', 'Thanking God', ['Gratitude']),
    ('scenario', 'Study Pressure', ['Peace', 'Guidance']),
    ('scenario', 'Overwhelmed', ['Peace', 'Prayer']),
    ('scenario', 'Keep Going', ['Strength', 'Growth']),
    ('scenario', 'Praying for Others', ['Prayer', 'Love']),
    ('scenario', 'Loss and Grief', ['Healing', 'Hope']),
    ('scenario', 'Closer to God', ['Faith', 'Prayer']),
    ('scenario', 'Holiday Season', ['Seasonal Content']),
    ('scenario', 'Daily Motivation', ['Growth', 'Strength']),
    ('scenario', 'Morning Reflection', ['Faith', 'Gratitude']),
    ('scenario', 'Celebration', ['Gratitude', 'Hope']),
    ('scenario', 'New Achievement', ['Gratitude', 'Self-Worth']),
    ('scenario', 'Personal Growth', ['Growth', 'Faith']),
    ('emotion', 'Anxious', ['Peace']),
    ('emotion', 'Afraid', ['Faith', 'Peace']),
    ('emotion', 'Sad', ['Hope', 'Healing']),
    ('emotion', 'Tired', ['Strength', 'Peace']),
    ('emotion', 'Lonely', ['Love', 'Hope']),
    ('emotion', 'Lost', ['Guidance']),
    ('emotion', 'Grateful', ['Gratitude']),
    ('emotion', 'Joyful', ['Gratitude']),
    ('emotion', 'Peaceful', ['Peace', 'Prayer']),
    ('emotion', 'Hopeful', ['Hope']),
    ('emotion', 'Hurt', ['Healing']),
    ('emotion', 'Encouraged', ['Growth']),
    ('visual', 'Warm Light', []),
    ('visual', 'Nature', []),
    ('visual', 'Minimal', []),
    ('visual', 'Abstract', []),
    ('visual', 'Night Sky', []),
    ('visual', 'Illustration', []),
]

DEFAULT_QUOTE_DISPLAY = [
    {'name_en': 'Peace', 'name_es': 'Paz', 'name_pt': 'Paz', 'mode': 'auto', 'match': 'all',
     'conditions': [{'type': 'theme', 'name': 'Peace'}], 'sort': 1, 'status': 1},
    {'name_en': 'For Sleep', 'name_es': 'Para dormir', 'name_pt': 'Para dormir', 'mode': 'auto', 'match': 'all',
     'conditions': [{'type': 'scenario', 'name': 'Before Sleep'}], 'sort': 2, 'status': 1},
    {'name_en': 'Encouragement', 'name_es': 'Ánimo', 'name_pt': 'Encorajamento', 'mode': 'auto', 'match': 'all',
     'conditions': [{'type': 'content', 'name': 'Christian Encouragement'}], 'sort': 3, 'status': 0},
    {'name_en': 'Daily Verse', 'name_es': 'Versículo diario', 'name_pt': 'Versículo diário', 'mode': 'auto', 'match': 'all',
     'conditions': [{'type': 'content', 'name': 'Bible Verse'}], 'sort': 4, 'status': 1},
    {'name_en': 'Morning', 'name_es': 'Mañana', 'name_pt': 'Manhã', 'mode': 'auto', 'match': 'any',
     'conditions': [{'type': 'scenario', 'name': 'Morning Start'}], 'sort': 5, 'status': 1},
    {'name_en': 'Seasonal', 'name_es': 'Temporada', 'name_pt': 'Sazonal', 'mode': 'auto', 'match': 'any',
     'conditions': [{'type': 'content', 'name': 'Seasonal Content'}], 'sort': 6, 'status': 1},
]

DEFAULT_WALLPAPER_DISPLAY = [
    {'name_en': 'Most Loved', 'name_es': 'Más populares', 'name_pt': 'Mais amados', 'mode': 'manual', 'match': 'any',
     'conditions': [], 'manual_ids': [], 'sort': 1, 'status': 1},
    {'name_en': 'Peaceful', 'name_es': 'Pacífico', 'name_pt': 'Pacífico', 'mode': 'auto', 'match': 'all',
     'conditions': [{'type': 'theme', 'name': 'Peace'}], 'sort': 2, 'status': 1},
    {'name_en': 'Warm Light', 'name_es': 'Luz cálida', 'name_pt': 'Luz quente', 'mode': 'auto', 'match': 'all',
     'conditions': [{'type': 'visual', 'name': 'Warm Light'}], 'sort': 3, 'status': 1},
    {'name_en': 'Morning', 'name_es': 'Mañana', 'name_pt': 'Manhã', 'mode': 'auto', 'match': 'any',
     'conditions': [{'type': 'scenario', 'name': 'Morning Start'}], 'sort': 4, 'status': 1},
    {'name_en': 'Quiet', 'name_es': 'Tranquilo', 'name_pt': 'Tranquilo', 'mode': 'auto', 'match': 'any',
     'conditions': [{'type': 'emotion', 'name': 'Peaceful'}], 'sort': 5, 'status': 1},
    {'name_en': 'Seasonal', 'name_es': 'Temporada', 'name_pt': 'Sazonal', 'mode': 'auto', 'match': 'any',
     'conditions': [{'type': 'scenario', 'name': 'Holiday Season'}], 'sort': 6, 'status': 0},
]

SCOPE_LABEL = {
    SCOPE_TEXT: '文字',
    SCOPE_WALLPAPER: '壁纸',
    SCOPE_BOTH: '文字／壁纸',
}

ZH_TYPE_LABEL = {
    'content': '文字内容类型',
    'visual': '壁纸视觉风格',
    'theme': '主题标签',
    'scenario': '生活场景',
    'emotion': '情绪标签',
}


def scope_applies(scope, surface):
    """surface: text | wallpaper"""
    if scope == SCOPE_BOTH:
        return True
    return scope == surface


def ensure_default_tag_library():
    """空库时写入原型预置；已有数据不覆盖运营改动。锁定类型始终纠正 locked/scope。"""
    created_any = False
    if not TagType.objects.exists():
        for row in DEFAULT_TYPES:
            TagType.objects.create(**row)
        created_any = True
    else:
        content = TagType.objects.filter(key='content').first()
        if content:
            TagType.objects.filter(pk=content.pk).update(
                locked=True, scope=SCOPE_TEXT, required=True, prefix='C', sort_order=10,
            )
        visual = TagType.objects.filter(key='visual').first()
        if visual:
            TagType.objects.filter(pk=visual.pk).update(
                locked=True, scope=SCOPE_WALLPAPER, required=True, prefix='V', sort_order=20,
            )
        for row in DEFAULT_TYPES:
            if not TagType.objects.filter(key=row['key']).exists():
                TagType.objects.create(**row)
                created_any = True
    if not Tag.objects.exists():
        counters = {}
        prefix_map = {t.key: t.prefix for t in TagType.objects.all()}
        for type_key, name, related in DEFAULT_TAGS:
            counters[type_key] = counters.get(type_key, 0) + 1
            prefix = prefix_map.get(type_key) or 'L'
            Tag.objects.create(
                type_key=type_key,
                code=f'{prefix}{counters[type_key]:02d}',
                name=name,
                enabled=name != 'Holiday Season',
                related_themes=related,
            )
        created_any = True
    else:
        # 增量补缺：兼容主题等新增种子，不覆盖已有
        prefix_map = {t.key: t.prefix for t in TagType.objects.all()}
        counters = {}
        for type_key, _name, _rel in Tag.objects.values_list('type_key', 'name', 'related_themes'):
            counters[type_key] = counters.get(type_key, 0) + 1
        for type_key, name, related in DEFAULT_TAGS:
            if Tag.objects.filter(type_key=type_key, name__iexact=name).exists():
                continue
            counters[type_key] = counters.get(type_key, 0) + 1
            prefix = prefix_map.get(type_key) or 'L'
            Tag.objects.create(
                type_key=type_key,
                code=f'{prefix}{counters[type_key]:02d}',
                name=name,
                enabled=True,
                related_themes=related,
            )
            created_any = True
    if not DisplayTag.objects.filter(kind=DisplayTag.KIND_QUOTE).exists():
        for row in DEFAULT_QUOTE_DISPLAY:
            DisplayTag.objects.create(
                kind=DisplayTag.KIND_QUOTE, display_type=DisplayTag.TYPE_COLLECTION, **row,
            )
        created_any = True
    if not DisplayTag.objects.filter(kind=DisplayTag.KIND_WALLPAPER).exists():
        for row in DEFAULT_WALLPAPER_DISPLAY:
            DisplayTag.objects.create(
                kind=DisplayTag.KIND_WALLPAPER, display_type=DisplayTag.TYPE_COLLECTION_GROUP, **row,
            )
        created_any = True
    from tools.display_structure import backfill_legacy_display_rows
    backfill_legacy_display_rows()
    return created_any


def next_tag_code(type_key):
    meta = TagType.objects.filter(key=type_key).first()
    prefix = (meta.prefix if meta else 'L') or 'L'
    max_n = 0
    for code in Tag.objects.filter(type_key=type_key).values_list('code', flat=True):
        m = re.search(r'(\d+)$', str(code or ''))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f'{prefix}{max_n + 1:02d}'


def slug_type_key(label):
    s = re.sub(r'[^a-z0-9]+', '_', (label or '').strip().lower()).strip('_')
    return s or 'custom'


def quote_values_for_type(row, type_key):
    if type_key == 'content':
        return [row.content_type] if getattr(row, 'content_type', '') else []
    if type_key == 'theme':
        return [row.theme] if getattr(row, 'theme', '') else []
    if type_key == 'scenario':
        return list(getattr(row, 'scenario_tags', None) or [])
    if type_key == 'emotion':
        return list(getattr(row, 'emotion_tags', None) or [])
    tax = getattr(row, 'taxonomy', None) or {}
    if not isinstance(tax, dict):
        return []
    return [x for x in _as_iterable(tax.get(type_key)) if x not in (None, '')]


def wallpaper_values_for_type(row, type_key):
    if type_key == 'theme':
        return [row.theme] if getattr(row, 'theme', '') else []
    if type_key == 'visual':
        return list(getattr(row, 'visual_tags', None) or [])
    if type_key == 'scenario':
        return list(getattr(row, 'scenario_tags', None) or [])
    if type_key == 'emotion':
        return list(getattr(row, 'emotion_tags', None) or [])
    tax = getattr(row, 'taxonomy', None) or {}
    if not isinstance(tax, dict):
        return []
    return [x for x in _as_iterable(tax.get(type_key)) if x not in (None, '')]


def _as_iterable(value):
    """把任意值转成可迭代列表：None→[]，list/tuple 原样，其它→[值]。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def build_tag_index(*, surface, content_type='', theme='', visual=None, scenario=None, emotion=None, taxonomy=None):
    keys = []
    if surface == 'text' and content_type:
        keys.append(f'content::{content_type}')
    if theme:
        keys.append(f'theme::{theme}')
    if surface == 'wallpaper':
        for name in _as_iterable(visual):
            keys.append(f'visual::{name}')
    for name in _as_iterable(scenario):
        keys.append(f'scenario::{name}')
    for name in _as_iterable(emotion):
        keys.append(f'emotion::{name}')
    for type_key, names in (taxonomy or {}).items():
        if type_key in ('content', 'theme', 'scenario', 'emotion', 'visual'):
            continue
        for name in _as_iterable(names):
            keys.append(f'{type_key}::{name}')
    return keys


def rebuild_quote_index(fields):
    fields['tag_index'] = build_tag_index(
        surface='text',
        content_type=fields.get('content_type') or '',
        theme=fields.get('theme') or '',
        scenario=fields.get('scenario_tags') or [],
        emotion=fields.get('emotion_tags') or [],
        taxonomy=fields.get('taxonomy') or {},
    )
    return fields


def rebuild_wallpaper_index(fields):
    fields['tag_index'] = build_tag_index(
        surface='wallpaper',
        theme=fields.get('theme') or '',
        visual=fields.get('visual_tags') or [],
        scenario=fields.get('scenario_tags') or [],
        emotion=fields.get('emotion_tags') or [],
        taxonomy=fields.get('taxonomy') or {},
    )
    return fields


def filter_queryset_by_display_tag(qs, kind, tag_name):
    """按已发布显性结构筛 Quote / Wallpaper；无快照时回落编辑态已启用项。"""
    from django.db.models import Q
    from types import SimpleNamespace
    from tools.display_structure import published_rows, serialize_wallpaper_filters
    tag_name = (tag_name or '').strip()
    if not tag_name:
        return qs
    if kind == DisplayTag.KIND_WALLPAPER:
        for item in serialize_wallpaper_filters(published=True):
            if item.get('frontend_enabled') and item.get('name') == tag_name:
                matched = [
                    obj.id for obj in qs
                    if tag_name in wallpaper_values_for_type(obj, item.get('type') or '')
                ]
                return qs.filter(id__in=matched) if matched else qs.none()
    rule = None
    for raw in published_rows(kind):
        if raw.get('name_en') != tag_name:
            continue
        if raw.get('status') not in ('published', 1):
            continue
        if raw.get('display_type') in (DisplayTag.TYPE_COLUMN, DisplayTag.TYPE_FILTER):
            continue
        rule = SimpleNamespace(
            kind=kind,
            mode=raw.get('mode') or DisplayTag.MODE_AUTO,
            match=raw.get('match') or DisplayTag.MATCH_ANY,
            conditions=raw.get('conditions') or [],
            manual_ids=raw.get('manual_ids') or [],
        )
        break
    if rule is None:
        row = DisplayTag.objects.filter(kind=kind, status=1).filter(
            Q(name_en=tag_name) | Q(name_es=tag_name) | Q(name_pt=tag_name),
        ).first()
        if row:
            rule = row
    if rule and rule.mode == DisplayTag.MODE_MANUAL:
        ids = []
        for raw in rule.manual_ids or []:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        return qs.filter(id__in=ids) if ids else qs.none()
    if rule and rule.mode == DisplayTag.MODE_AUTO:
        matched = [obj.id for obj in qs if item_matches_display(kind, obj, rule)]
        return qs.filter(id__in=matched) if matched else qs.none()
    return qs.filter(display_tags__contains=[tag_name])


def missing_languages(content, content_es=None, content_pt=None):
    """本期仅校验 English；西语/葡语字段保留兼容，不再作为发布门禁。"""
    if not str(content or '').strip():
        return ['English']
    return []


def missing_required_types(surface, getter):
    """getter(type_key) -> list of names."""
    missing = []
    for t in TagType.objects.filter(required=True):
        if not scope_applies(t.scope, surface):
            continue
        if not getter(t.key):
            missing.append(ZH_TYPE_LABEL.get(t.key) or t.label)
    return missing


def item_matches_display(kind, item, row):
    if row.mode == DisplayTag.MODE_MANUAL:
        try:
            return int(item.id) in [int(x) for x in (row.manual_ids or [])]
        except (TypeError, ValueError):
            return False
    conditions = row.conditions or []
    if not conditions:
        return False
    fn = quote_values_for_type if kind == DisplayTag.KIND_QUOTE else wallpaper_values_for_type
    results = []
    for cond in conditions:
        type_key = cond.get('type') or cond.get('type_key') or ''
        name = cond.get('name') or ''
        results.append(name in fn(item, type_key))
    if row.match == DisplayTag.MATCH_ALL:
        return all(results)
    return any(results)


def display_tag_count(row):
    display_type = getattr(row, 'display_type', '') or DisplayTag.TYPE_COLLECTION
    if display_type == DisplayTag.TYPE_COLUMN:
        return DisplayTag.objects.filter(
            kind=row.kind, display_type=DisplayTag.TYPE_COLLECTION, parent_id=row.id,
        ).count()
    if display_type == DisplayTag.TYPE_FILTER:
        return 0
    if row.mode == DisplayTag.MODE_MANUAL:
        return len(row.manual_ids or [])
    Model = Quote if row.kind == DisplayTag.KIND_QUOTE else Wallpaper
    qs = Model.objects.filter(is_deleted=False)
    n = 0
    for item in qs.iterator():
        if item_matches_display(row.kind, item, row):
            n += 1
    return n


def display_tag_enabled_count(row):
    display_type = getattr(row, 'display_type', '') or DisplayTag.TYPE_COLLECTION
    if display_type in (DisplayTag.TYPE_COLUMN, DisplayTag.TYPE_FILTER):
        return 0
    Model = Quote if row.kind == DisplayTag.KIND_QUOTE else Wallpaper
    if row.mode == DisplayTag.MODE_MANUAL:
        ids = []
        for raw in row.manual_ids or []:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not ids:
            return 0
        return Model.objects.filter(id__in=ids, is_deleted=False, status=1).count()
    n = 0
    for item in Model.objects.filter(is_deleted=False, status=1).iterator():
        if item_matches_display(row.kind, item, row):
            n += 1
    return n


def resolved_display_names(kind, item, rules=None):
    """手动勾选 ∪ 已启用自动规则命中。"""
    names = list(getattr(item, 'display_tags', None) or [])
    if rules is None:
        rules = list(DisplayTag.objects.filter(kind=kind, status=1))
    for row in rules:
        if row.mode == DisplayTag.MODE_AUTO and item_matches_display(kind, item, row):
            if row.name_en not in names:
                names.append(row.name_en)
    return names


def tag_use_counts():
    """type::name -> 语录+壁纸条数（含停用内容，不含软删）。"""
    counts = {}
    for idx in Quote.objects.filter(is_deleted=False).values_list('tag_index', flat=True):
        for key in idx or []:
            counts[key] = counts.get(key, 0) + 1
    for idx in Wallpaper.objects.filter(is_deleted=False).values_list('tag_index', flat=True):
        for key in idx or []:
            counts[key] = counts.get(key, 0) + 1
    return counts


def serialize_type(t, tag_count=None):
    return {
        'key': t.key,
        'label': t.label,
        'label_zh': ZH_TYPE_LABEL.get(t.key) or t.label,
        'prefix': t.prefix,
        'scope': t.scope,
        'scope_label': SCOPE_LABEL.get(t.scope, t.scope),
        'required': t.required,
        'is_multi': t.is_multi,
        'locked': t.locked,
        'sort_order': t.sort_order,
        'enabled': t.enabled,
        'tag_count': tag_count,
    }


def serialize_tag(tag, uses=0):
    return {
        'id': tag.id,
        'type': tag.type_key,
        'code': tag.code,
        'name': tag.name,
        'enabled': tag.enabled,
        'related_themes': tag.related_themes or [],
        'uses': uses,
    }


def serialize_display_tag(row, count=None, enabled_count=None, parent_name=''):
    if count is None:
        count = display_tag_count(row)
    if enabled_count is None and getattr(row, 'display_type', '') in (
        DisplayTag.TYPE_COLLECTION, DisplayTag.TYPE_COLLECTION_GROUP, '',
    ):
        enabled_count = display_tag_enabled_count(row)
    return {
        'id': row.id,
        'kind': row.kind,
        'display_type': getattr(row, 'display_type', '') or DisplayTag.TYPE_COLLECTION,
        'parent_id': getattr(row, 'parent_id', None),
        'parent_name': parent_name,
        'source_tag_id': getattr(row, 'source_tag_id', None),
        'name_en': row.name_en,
        'name_es': row.name_es,
        'name_pt': row.name_pt,
        'sort': row.sort,
        'vip': bool(getattr(row, 'vip', False)),
        'status': 'published' if row.status == 1 else 'disabled',
        'mode': row.mode,
        'match': row.match,
        'conditions': row.conditions or [],
        'manual_ids': row.manual_ids or [],
        'count': count,
        'enabled_count': enabled_count or 0,
    }


def rename_tag_everywhere(type_key, old_name, new_name):
    if old_name == new_name:
        return
    old_key = f'{type_key}::{old_name}'
    new_key = f'{type_key}::{new_name}'

    def swap_list(vals):
        return [new_name if x == old_name else x for x in (vals or [])]

    if type_key == 'content':
        Quote.objects.filter(content_type=old_name).update(content_type=new_name)
    if type_key == 'theme':
        Quote.objects.filter(theme=old_name).update(theme=new_name)
        Wallpaper.objects.filter(theme=old_name).update(theme=new_name)
        for q in Quote.objects.filter(is_deleted=False).only('id', 'interest_tags'):
            if old_name in (q.interest_tags or []):
                Quote.objects.filter(pk=q.pk).update(
                    interest_tags=[new_name if x == old_name else x for x in (q.interest_tags or [])],
                )
        for tag in Tag.objects.exclude(type_key='theme'):
            rel = tag.related_themes or []
            if old_name in rel:
                Tag.objects.filter(pk=tag.pk).update(
                    related_themes=[new_name if x == old_name else x for x in rel],
                )
    if type_key == 'scenario':
        for q in Quote.objects.filter(is_deleted=False).only('id', 'scenario_tags'):
            if old_name in (q.scenario_tags or []):
                Quote.objects.filter(pk=q.pk).update(scenario_tags=swap_list(q.scenario_tags))
        for w in Wallpaper.objects.filter(is_deleted=False).only('id', 'scenario_tags'):
            if old_name in (w.scenario_tags or []):
                Wallpaper.objects.filter(pk=w.pk).update(scenario_tags=swap_list(w.scenario_tags))
    if type_key == 'emotion':
        for q in Quote.objects.filter(is_deleted=False).only('id', 'emotion_tags'):
            if old_name in (q.emotion_tags or []):
                Quote.objects.filter(pk=q.pk).update(emotion_tags=swap_list(q.emotion_tags))
        for w in Wallpaper.objects.filter(is_deleted=False).only('id', 'emotion_tags'):
            if old_name in (w.emotion_tags or []):
                Wallpaper.objects.filter(pk=w.pk).update(emotion_tags=swap_list(w.emotion_tags))
    if type_key == 'visual':
        for w in Wallpaper.objects.filter(is_deleted=False).only('id', 'visual_tags'):
            if old_name in (w.visual_tags or []):
                Wallpaper.objects.filter(pk=w.pk).update(visual_tags=swap_list(w.visual_tags))

    for Model in (Quote, Wallpaper):
        for row in Model.objects.filter(is_deleted=False):
            tax = dict(row.taxonomy or {})
            changed = False
            if type_key in tax:
                tax[type_key] = swap_list(tax[type_key])
                changed = True
            idx = list(row.tag_index or [])
            if old_key in idx:
                idx = [new_key if x == old_key else x for x in idx]
                changed = True
            if changed:
                Model.objects.filter(pk=row.pk).update(taxonomy=tax, tag_index=idx)

    for d in DisplayTag.objects.all():
        conds = d.conditions or []
        new_conds = []
        changed = False
        for c in conds:
            if (c.get('type') or c.get('type_key')) == type_key and c.get('name') == old_name:
                new_conds.append({**c, 'name': new_name, 'type': type_key})
                changed = True
            else:
                new_conds.append(c)
        if changed:
            DisplayTag.objects.filter(pk=d.pk).update(conditions=new_conds)


def rename_display_tag_everywhere(kind, old_name, new_name):
    if old_name == new_name:
        return
    Model = Quote if kind == DisplayTag.KIND_QUOTE else Wallpaper
    for row in Model.objects.filter(is_deleted=False):
        tags = list(row.display_tags or [])
        if old_name in tags:
            Model.objects.filter(pk=row.pk).update(
                display_tags=[new_name if x == old_name else x for x in tags],
            )


def wallpaper_use_map():
    rows = WallpaperUsage.objects.values('wallpaper_id').annotate(n=Count('id'))
    return {r['wallpaper_id']: r['n'] for r in rows}
