#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""显性结构：编辑态脏标记、发布快照、收录预览、壁纸筛选同步。"""
from django.db import transaction
from django.utils import timezone

from models.models import DisplayTag, DisplayStructureState, Quote, Tag, TagType, Wallpaper
from tools.tag_library import (
    SCOPE_WALLPAPER, SCOPE_BOTH, _as_iterable, item_matches_display, quote_values_for_type,
    serialize_display_tag, serialize_tag, wallpaper_values_for_type,
)


def get_structure_state(kind):
    state, _ = DisplayStructureState.objects.get_or_create(kind=kind)
    return state


def mark_structure_dirty(kind):
    state = get_structure_state(kind)
    if not state.dirty:
        DisplayStructureState.objects.filter(pk=state.pk).update(dirty=True)
        state.dirty = True
    publish_structure(kind)
    return state


def backfill_legacy_display_rows():
    """把旧扁平展示标签归入模块/合集，不覆盖已有层级。"""
    quote_rows = list(DisplayTag.objects.filter(kind=DisplayTag.KIND_QUOTE))
    if quote_rows and not any(r.display_type == DisplayTag.TYPE_COLUMN for r in quote_rows):
        orphans = [r for r in quote_rows if (r.display_type or '') in ('', DisplayTag.TYPE_COLLECTION)]
        if orphans:
            column = DisplayTag.objects.create(
                kind=DisplayTag.KIND_QUOTE,
                display_type=DisplayTag.TYPE_COLUMN,
                name_en='By type',
                sort=1,
                status=1,
                mode=DisplayTag.MODE_MANUAL,
            )
            for idx, row in enumerate(orphans, start=1):
                DisplayTag.objects.filter(pk=row.pk).update(
                    display_type=DisplayTag.TYPE_COLLECTION,
                    parent_id=column.id,
                    sort=row.sort or idx,
                )
    wallpaper_rows = DisplayTag.objects.filter(kind=DisplayTag.KIND_WALLPAPER)
    wallpaper_rows.exclude(
        display_type__in=(DisplayTag.TYPE_COLLECTION_GROUP, DisplayTag.TYPE_FILTER),
    ).update(display_type=DisplayTag.TYPE_COLLECTION_GROUP)


def parent_name_map(kind):
    names = {}
    for row in DisplayTag.objects.filter(kind=kind, display_type=DisplayTag.TYPE_COLUMN):
        names[row.id] = row.name_en
    return names


def serialize_edit_rows(kind):
    parents = parent_name_map(kind)
    rows = DisplayTag.objects.filter(kind=kind).exclude(display_type=DisplayTag.TYPE_FILTER).order_by('sort', 'id')
    return [serialize_display_tag(r, parent_name=parents.get(r.parent_id) or '') for r in rows]


def item_secondary_tags(kind, item):
    if kind == DisplayTag.KIND_QUOTE:
        values = [
            getattr(item, 'content_type', '') or '',
            getattr(item, 'theme', '') or '',
            *(getattr(item, 'scenario_tags', None) or []),
            *(getattr(item, 'emotion_tags', None) or []),
        ]
        tax = getattr(item, 'taxonomy', None) or {}
        if isinstance(tax, dict):
            for names in tax.values():
                values.extend(_as_iterable(names))
        return [x for x in dict.fromkeys(values) if x]
    values = [
        getattr(item, 'theme', '') or '',
        *(getattr(item, 'visual_tags', None) or []),
        *(getattr(item, 'scenario_tags', None) or []),
        *(getattr(item, 'emotion_tags', None) or []),
    ]
    tax = getattr(item, 'taxonomy', None) or {}
    if isinstance(tax, dict):
        for names in tax.values():
            values.extend(_as_iterable(names))
    return [x for x in dict.fromkeys(values) if x]


def matched_condition_names(kind, item, conditions):
    fn = quote_values_for_type if kind == DisplayTag.KIND_QUOTE else wallpaper_values_for_type
    names = []
    for cond in conditions or []:
        name = cond.get('name') or ''
        type_key = cond.get('type') or cond.get('type_key') or ''
        if name and name in fn(item, type_key):
            names.append(name)
    return names


def serialize_preview_item(kind, item, conditions=None):
    tags = item_secondary_tags(kind, item)
    matched = matched_condition_names(kind, item, conditions)
    if kind == DisplayTag.KIND_QUOTE:
        return {
            'id': item.id,
            'title': item.content or '',
            'status': 1 if item.status == 1 else 0,
            'publish_status': 'published' if item.status == 1 else 'disabled',
            'tags': tags,
            'matched_tags': matched,
            'vip': bool(getattr(item, 'is_vip', False)),
        }
    thumb = (item.poster_url if getattr(item, 'media_type', '') == 'video' else item.image_url) or item.image_url
    return {
        'id': item.id,
        'title': item.name_en or '',
        'thumb': thumb or '',
        'status': 1 if item.status == 1 else 0,
        'publish_status': 'published' if item.status == 1 else 'disabled',
        'tags': tags,
        'matched_tags': matched,
        'vip': bool(item.is_vip),
    }


def preview_matches(kind, mode, match, conditions, manual_ids):
    Model = Quote if kind == DisplayTag.KIND_QUOTE else Wallpaper
    rule = DisplayTag(
        kind=kind, mode=mode, match=match,
        conditions=conditions or [], manual_ids=manual_ids or [],
    )
    items = []
    for item in Model.objects.filter(is_deleted=False).order_by('-id').iterator():
        if item_matches_display(kind, item, rule):
            items.append(serialize_preview_item(kind, item, conditions if mode == DisplayTag.MODE_AUTO else []))
    enabled = sum(1 for x in items if x['status'] == 1)
    return {
        'items': items,
        'count': len(items),
        'enabled_count': enabled,
    }


def wallpaper_scope_types():
    return list(TagType.objects.filter(scope__in=(SCOPE_WALLPAPER, SCOPE_BOTH)).order_by('sort_order', 'id'))


def sync_wallpaper_filters(state=None):
    """草稿默认关闭；已有键保留。"""
    state = state or get_structure_state(DisplayTag.KIND_WALLPAPER)
    draft = dict(state.wallpaper_filters or {})
    published = dict(state.wallpaper_filters_published or {})
    type_keys = [t.key for t in wallpaper_scope_types()]
    changed = False
    for tag in Tag.objects.filter(type_key__in=type_keys):
        key = str(tag.id)
        if key not in draft:
            draft[key] = False
            changed = True
        if key not in published:
            published[key] = False
            changed = True
    if changed:
        DisplayStructureState.objects.filter(pk=state.pk).update(
            wallpaper_filters=draft, wallpaper_filters_published=published,
        )
        state.wallpaper_filters = draft
        state.wallpaper_filters_published = published
    return state


def serialize_wallpaper_filters(state=None, published=False):
    state = sync_wallpaper_filters(state)
    flags = state.wallpaper_filters_published if published else state.wallpaper_filters
    types = {t.key: t for t in wallpaper_scope_types()}
    items = []
    for tag in Tag.objects.filter(type_key__in=types.keys()).order_by('type_key', 'id'):
        meta = types.get(tag.type_key)
        show = bool((flags or {}).get(str(tag.id)))
        items.append({
            **serialize_tag(tag),
            'type_label': (meta.label if meta else tag.type_key),
            'display_enabled': show,
            'frontend_enabled': bool(tag.enabled and show),
        })
    return items


def snapshot_rows(kind):
    parents = parent_name_map(kind)
    data = []
    qs = DisplayTag.objects.filter(kind=kind).exclude(display_type=DisplayTag.TYPE_FILTER).order_by('sort', 'id')
    for row in qs:
        data.append(serialize_display_tag(row, parent_name=parents.get(row.parent_id) or ''))
    return data


def publish_structure(kind, operator=''):
    state = get_structure_state(kind)
    snapshot = {'rows': snapshot_rows(kind)}
    filters = {}
    if kind == DisplayTag.KIND_WALLPAPER:
        state = sync_wallpaper_filters(state)
        filters = dict(state.wallpaper_filters or {})
        snapshot['wallpaper_filters'] = filters
    DisplayStructureState.objects.filter(pk=state.pk).update(
        dirty=False,
        published_version=(state.published_version or 0) + 1,
        wallpaper_filters_published=filters or state.wallpaper_filters_published,
        published_snapshot=snapshot,
        published_by=operator or '',
        published_at=timezone.now(),
    )
    state.refresh_from_db()
    return state


def _status_from_snapshot(value):
    if value in ('published', 1, '1', True):
        return 1
    return 0


def _optional_int(value):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fields_from_snapshot_row(raw, kind):
    """把已发布快照里的一条显性标签还原成 DisplayTag 可写字段。"""
    try:
        sort = int(raw.get('sort') or 1)
    except (TypeError, ValueError):
        sort = 1
    return {
        'kind': kind,
        'display_type': raw.get('display_type') or DisplayTag.TYPE_COLLECTION,
        'parent_id': _optional_int(raw.get('parent_id')),
        'source_tag_id': _optional_int(raw.get('source_tag_id')),
        'name_en': raw.get('name_en') or '',
        'name_es': raw.get('name_es') or '',
        'name_pt': raw.get('name_pt') or '',
        'sort': sort,
        'status': _status_from_snapshot(raw.get('status')),
        'vip': bool(raw.get('vip')),
        'mode': raw.get('mode') or DisplayTag.MODE_AUTO,
        'match': raw.get('match') or DisplayTag.MATCH_ANY,
        'conditions': raw.get('conditions') if isinstance(raw.get('conditions'), list) else [],
        'manual_ids': raw.get('manual_ids') if isinstance(raw.get('manual_ids'), list) else [],
    }


def discard_structure(kind):
    """放弃未发布编辑，把显性结构恢复成上次发布快照。

    从未发布过时，编辑态清空。壁纸同时把筛选开关草稿退回已发布值。
    改过展示名的，内容上的 display_tags 一并改回去。
    """
    from tools.tag_library import rename_display_tag_everywhere

    state = get_structure_state(kind)
    snap = state.published_snapshot or {}
    if 'rows' in snap and isinstance(snap.get('rows'), list):
        rows = snap.get('rows') or []
    elif state.published_version:
        rows = published_rows(kind)
    else:
        rows = []

    with transaction.atomic():
        current = {
            row.id: row
            for row in DisplayTag.objects.filter(kind=kind).exclude(display_type=DisplayTag.TYPE_FILTER)
        }
        keep_ids = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            fields = fields_from_snapshot_row(raw, kind)
            row_id = _optional_int(raw.get('id'))
            existing = current.get(row_id) if row_id else None
            if existing and existing.name_en != fields['name_en']:
                rename_display_tag_everywhere(kind, existing.name_en, fields['name_en'])
            if existing:
                DisplayTag.objects.filter(pk=existing.pk).update(**fields)
                keep_ids.append(existing.pk)
            elif row_id:
                DisplayTag.objects.create(pk=row_id, **fields)
                keep_ids.append(row_id)
            else:
                created = DisplayTag.objects.create(**fields)
                keep_ids.append(created.pk)

        stale = DisplayTag.objects.filter(kind=kind).exclude(display_type=DisplayTag.TYPE_FILTER)
        if keep_ids:
            stale = stale.exclude(pk__in=keep_ids)
        stale.delete()

        update = {'dirty': False}
        if kind == DisplayTag.KIND_WALLPAPER:
            filters = snap.get('wallpaper_filters')
            if not isinstance(filters, dict):
                filters = dict(state.wallpaper_filters_published or {})
            update['wallpaper_filters'] = filters
        DisplayStructureState.objects.filter(pk=state.pk).update(**update)

    state.refresh_from_db()
    return state


def published_rows(kind):
    state = get_structure_state(kind)
    snap = state.published_snapshot or {}
    rows = snap.get('rows')
    if rows is not None and state.published_version:
        return rows
    return [
        serialize_display_tag(r)
        for r in DisplayTag.objects.filter(kind=kind, status=1).order_by('sort', 'id')
        if (r.display_type or DisplayTag.TYPE_COLLECTION) != DisplayTag.TYPE_FILTER
    ]


def _id_desc(rows):
    def _key(row):
        try:
            return int((row or {}).get('id') or 0)
        except (TypeError, ValueError):
            return 0
    return sorted(rows or [], key=_key, reverse=True)


def serialize_structure(kind):
    state = get_structure_state(kind)
    if kind == DisplayTag.KIND_WALLPAPER:
        state = sync_wallpaper_filters(state)
    filters = serialize_wallpaper_filters(state) if kind == DisplayTag.KIND_WALLPAPER else []
    published_filters = serialize_wallpaper_filters(state, published=True) if kind == DisplayTag.KIND_WALLPAPER else []
    return {
        'kind': kind,
        'dirty': bool(state.dirty),
        'published_version': state.published_version or 0,
        'published_by': state.published_by or '',
        'published_at': state.published_at.isoformat() if state.published_at else '',
        'rows': _id_desc(serialize_edit_rows(kind)),
        'published_rows': _id_desc((state.published_snapshot or {}).get('rows') or []),
        'wallpaper_filters': _id_desc(filters),
        'published_wallpaper_filters': _id_desc(published_filters),
    }
