#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""AdminOps mixin — 标签类型 / 归类标签 / 前端展示标签 / 语录批量打标。"""
from django.db.models import Count, Max
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from Apps.views.admin_ops._helpers import (  # noqa: F401
    Tag, TagType, DisplayTag, Quote, IdSerializer, PERM_CONTENT, has_perm,
    ApiResponse, CustomPagination, _deny_app_write,
)
from Apps.views.admin_ops.serializers import BatchIdSerializer
from tools.display_structure import mark_structure_dirty
from tools.tag_library import (
    ensure_default_tag_library, next_tag_code, slug_type_key, serialize_type, serialize_tag,
    serialize_display_tag, tag_use_counts, rename_tag_everywhere, rename_display_tag_everywhere,
    SCOPE_TEXT, SCOPE_WALLPAPER, SCOPE_BOTH, rebuild_quote_index, ensure_tag_exists,
)


class TagLibraryMixin:
    @extend_schema(summary=_('标签类型列表'))
    @action(detail=False, methods=['get'], url_path='tag-types')
    def tag_types(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ensure_default_tag_library()
        counts = {
            row['type_key']: row['n']
            for row in Tag.objects.values('type_key').annotate(n=Count('id'))
        }
        data = [serialize_type(t, counts.get(t.key, 0)) for t in TagType.objects.all()]
        return ApiResponse(data=data)

    @extend_schema(summary=_('标签类型保存'))
    @action(detail=False, methods=['post'], url_path='tag-type-save')
    def tag_type_save(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ensure_default_tag_library()
        key = (request.data.get('key') or '').strip()
        label = (request.data.get('label') or '').strip()
        if not label:
            return ApiResponse(message=_('请填写 English 类型名称'), code=400)
        scope = (request.data.get('scope') or SCOPE_TEXT).strip()
        if scope in ('文字', 'text'):
            scope = SCOPE_TEXT
        elif scope in ('壁纸', 'wallpaper'):
            scope = SCOPE_WALLPAPER
        elif scope in ('文字／壁纸', 'both', '文字/壁纸'):
            scope = SCOPE_BOTH
        if scope not in (SCOPE_TEXT, SCOPE_WALLPAPER, SCOPE_BOTH):
            return ApiResponse(message=_('适用内容不合法'), code=400)
        required = bool(request.data.get('required'))
        is_multi = bool(request.data.get('is_multi'))
        dup = TagType.objects.filter(label__iexact=label)
        if key:
            existing = TagType.objects.filter(key=key).first()
            if not existing:
                return ApiResponse(message='not found', code=404)
            if existing.locked:
                return ApiResponse(message=_('系统固定标签类型不能修改'), code=400)
            if dup.exclude(pk=existing.pk).exists():
                return ApiResponse(message=_('已存在同名标签类型'), code=400)
            TagType.objects.filter(pk=existing.pk).update(label=label, scope=scope, required=required, is_multi=is_multi)
            return ApiResponse(data={'key': existing.key})
        if dup.exists():
            return ApiResponse(message=_('已存在同名标签类型'), code=400)
        base = slug_type_key(label)
        new_key = base
        i = 2
        while TagType.objects.filter(key=new_key).exists():
            new_key = f'{base}_{i}'
            i += 1
        max_sort = TagType.objects.aggregate(m=Max('sort_order')).get('m') or 50
        t = TagType.objects.create(
            key=new_key, label=label, prefix='L', scope=scope, required=required, is_multi=is_multi,
            locked=False, sort_order=max_sort + 10,
        )
        return ApiResponse(data={'key': t.key}, code=201)

    @extend_schema(summary=_('归类标签列表'))
    @action(detail=False, methods=['get'], url_path='tags')
    def tags(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ensure_default_tag_library()
        qs = Tag.objects.all().order_by('-id')
        type_key = (request.query_params.get('type') or '').strip()
        if type_key:
            qs = qs.filter(type_key=type_key)
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(name__icontains=q)
        enabled = request.query_params.get('enabled')
        if enabled in ('1', 'true', 'True', 'enabled'):
            qs = qs.filter(enabled=True)
        elif enabled in ('0', 'false', 'False', 'disabled'):
            qs = qs.filter(enabled=False)
        uses = tag_use_counts()
        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        data = [serialize_tag(t, uses.get(f'{t.type_key}::{t.name}', 0)) for t in page]
        return paginator.get_paginated_response(data)

    @extend_schema(summary=_('归类标签保存'))
    @action(detail=False, methods=['post'], url_path='tag-save')
    def tag_save(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ensure_default_tag_library()
        type_key = (request.data.get('type') or request.data.get('type_key') or '').strip()
        name = (request.data.get('name') or '').strip()
        if not type_key or not TagType.objects.filter(key=type_key).exists():
            return ApiResponse(message=_('标签类型不存在'), code=400)
        if not name:
            return ApiResponse(message=_('请填写 English 标签名称'), code=400)
        enabled = request.data.get('enabled')
        if enabled is None:
            enabled = True
        related = request.data.get('related_themes') or []
        if not isinstance(related, list):
            related = []
        tid = request.data.get('id')
        dup = Tag.objects.filter(type_key=type_key, name__iexact=name)
        if tid:
            row = Tag.objects.filter(id=tid).first()
            if not row:
                return ApiResponse(message='not found', code=404)
            if dup.exclude(pk=row.pk).exists():
                return ApiResponse(message=_('当前类型下已存在同名标签'), code=400)
            old_name = row.name
            Tag.objects.filter(pk=row.pk).update(
                name=name, enabled=bool(enabled), related_themes=related,
            )
            if old_name != name:
                rename_tag_everywhere(type_key, old_name, name)
            return ApiResponse(data={'id': row.id})
        if dup.exists():
            return ApiResponse(message=_('当前类型下已存在同名标签'), code=400)
        row = Tag.objects.create(
            type_key=type_key, name=name, code=next_tag_code(type_key),
            enabled=bool(enabled), related_themes=related,
        )
        return ApiResponse(data={'id': row.id, 'code': row.code}, code=201)

    @extend_schema(summary=_('归类标签启停'))
    @action(detail=False, methods=['post'], url_path='tag-status')
    def tag_status(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ser = IdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        row = Tag.objects.filter(id=ser.validated_data['id']).first()
        if not row:
            return ApiResponse(message='not found', code=404)
        enabled = request.data.get('enabled')
        if enabled is None:
            enabled = not row.enabled
        Tag.objects.filter(pk=row.pk).update(enabled=bool(enabled))
        return ApiResponse(data={'id': row.id, 'enabled': bool(enabled)})

    @extend_schema(summary=_('前端展示标签列表'))
    @action(detail=False, methods=['get'], url_path='display-tags')
    def display_tags(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ensure_default_tag_library()
        kind = (request.query_params.get('kind') or DisplayTag.KIND_QUOTE).strip()
        if kind not in (DisplayTag.KIND_QUOTE, DisplayTag.KIND_WALLPAPER):
            return ApiResponse(message=_('kind 须为 quote 或 wallpaper'), code=400)
        qs = DisplayTag.objects.filter(kind=kind).order_by('sort', 'id')
        return ApiResponse(data=[serialize_display_tag(r) for r in qs])

    @extend_schema(summary=_('前端展示标签保存'))
    @action(detail=False, methods=['post'], url_path='display-tag-save')
    def display_tag_save(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ensure_default_tag_library()
        kind = (request.data.get('kind') or '').strip()
        if kind not in (DisplayTag.KIND_QUOTE, DisplayTag.KIND_WALLPAPER):
            return ApiResponse(message=_('kind 须为 quote 或 wallpaper'), code=400)
        name_en = (request.data.get('name_en') or '').strip()
        name_es = (request.data.get('name_es') or '').strip()
        name_pt = (request.data.get('name_pt') or '').strip()
        if not name_en:
            return ApiResponse(message=_('请填写 English 展示名称'), code=400)
        display_type = (request.data.get('display_type') or '').strip()
        allowed_types = {
            DisplayTag.KIND_QUOTE: {DisplayTag.TYPE_COLUMN, DisplayTag.TYPE_COLLECTION},
            DisplayTag.KIND_WALLPAPER: {DisplayTag.TYPE_COLLECTION_GROUP},
        }
        if not display_type:
            display_type = (
                DisplayTag.TYPE_COLLECTION if kind == DisplayTag.KIND_QUOTE
                else DisplayTag.TYPE_COLLECTION_GROUP
            )
        if display_type not in allowed_types[kind]:
            return ApiResponse(message=_('显性对象类型不合法'), code=400)
        parent_id = request.data.get('parent_id')
        try:
            parent_id = int(parent_id) if parent_id not in (None, '') else None
        except (TypeError, ValueError):
            parent_id = None
        if display_type == DisplayTag.TYPE_COLLECTION:
            parent = DisplayTag.objects.filter(
                id=parent_id, kind=kind, display_type=DisplayTag.TYPE_COLUMN, status=1,
            ).first() if parent_id else None
            if not parent:
                return ApiResponse(message=_('请选择所属文字模块'), code=400)
            parent_id = parent.id
        else:
            parent_id = None
        vip = bool(request.data.get('vip')) if display_type == DisplayTag.TYPE_COLLECTION else False
        try:
            sort = int(request.data.get('sort') or 1)
        except (TypeError, ValueError):
            sort = 0
        if sort < 1:
            return ApiResponse(message=_('显示排序必须为大于 0 的整数'), code=400)
        mode = (request.data.get('mode') or DisplayTag.MODE_AUTO).strip()
        if mode not in (DisplayTag.MODE_AUTO, DisplayTag.MODE_MANUAL):
            return ApiResponse(message=_('收录方式不合法'), code=400)
        match = (request.data.get('match') or DisplayTag.MATCH_ANY).strip()
        if match not in (DisplayTag.MATCH_ANY, DisplayTag.MATCH_ALL):
            match = DisplayTag.MATCH_ANY
        conditions = request.data.get('conditions') or []
        manual_ids = request.data.get('manual_ids') or []
        if not isinstance(conditions, list):
            conditions = []
        if not isinstance(manual_ids, list):
            manual_ids = []
        needs_rule = display_type == DisplayTag.TYPE_COLLECTION
        if needs_rule and mode == DisplayTag.MODE_AUTO and not conditions:
            return ApiResponse(message=_('请至少选择一个自动收录条件'), code=400)
        if needs_rule and mode == DisplayTag.MODE_MANUAL and not manual_ids:
            return ApiResponse(message=_('请至少选择一条语录'), code=400)
        if display_type == DisplayTag.TYPE_COLUMN:
            mode = DisplayTag.MODE_MANUAL
            conditions = []
            manual_ids = []
        status_raw = request.data.get('status')
        if status_raw in ('published', 1, '1', True):
            status = 1
        elif status_raw in ('disabled', 0, '0', False):
            status = 0
        else:
            status = 1 if str(status_raw).lower() in ('published', '1', 'true') else 0
        did = request.data.get('id')
        dup = DisplayTag.objects.filter(kind=kind, display_type=display_type, name_en__iexact=name_en)
        if did:
            row = DisplayTag.objects.filter(id=did, kind=kind).first()
            if not row:
                return ApiResponse(message='not found', code=404)
            if row.display_type == DisplayTag.TYPE_COLUMN and row.status == 1 and status == 0:
                pass
            if dup.exclude(pk=row.pk).exists():
                return ApiResponse(message=_('已存在相同的 English 展示名称'), code=400)
            old = row.name_en
            DisplayTag.objects.filter(pk=row.pk).update(
                name_en=name_en, name_es=name_es, name_pt=name_pt, sort=sort,
                display_type=display_type, parent_id=parent_id, vip=vip,
                mode=mode, match=match, conditions=conditions, manual_ids=manual_ids, status=status,
            )
            if old != name_en:
                rename_display_tag_everywhere(kind, old, name_en)
            row.refresh_from_db()
            mark_structure_dirty(kind)
            return ApiResponse(data=serialize_display_tag(row))
        if dup.exists():
            return ApiResponse(message=_('已存在相同的 English 展示名称'), code=400)
        row = DisplayTag.objects.create(
            kind=kind, display_type=display_type, parent_id=parent_id, vip=vip,
            name_en=name_en, name_es=name_es, name_pt=name_pt, sort=sort,
            mode=mode, match=match, conditions=conditions, manual_ids=manual_ids, status=status,
        )
        mark_structure_dirty(kind)
        return ApiResponse(data=serialize_display_tag(row), code=201)

    @extend_schema(summary=_('前端展示标签启停'))
    @action(detail=False, methods=['post'], url_path='display-tag-status')
    def display_tag_status(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ser = IdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        row = DisplayTag.objects.filter(id=ser.validated_data['id']).first()
        if not row:
            return ApiResponse(message='not found', code=404)
        status_raw = request.data.get('status')
        if status_raw in ('published', 1, '1', True):
            status = 1
        elif status_raw in ('disabled', 0, '0', False):
            status = 0
        else:
            status = 0 if row.status == 1 else 1
        DisplayTag.objects.filter(pk=row.pk).update(status=status)
        mark_structure_dirty(row.kind)
        return ApiResponse(data={'id': row.id, 'status': 'published' if status == 1 else 'disabled'})

    @extend_schema(summary=_('前端展示标签删除（支持批量）'))
    @action(detail=False, methods=['post'], url_path='display-tag-delete')
    def display_tag_delete(self, request):
        """硬删显性标签（编辑态结构改了后会标记 dirty，需重新发布）。

        入参：{"id": 1} 单删 或 {"ids": [1,2]} 批量。
        """
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ser = BatchIdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ids = ser.get_ids()
        rows = DisplayTag.objects.filter(id__in=ids)
        if rows.exists():
            kinds = set(rows.values_list('kind', flat=True))
            deleted = rows.count()
            rows.delete()
            for k in kinds:
                mark_structure_dirty(k)
        else:
            deleted = 0
        return ApiResponse(data={'deleted': deleted})

    @extend_schema(summary=_('语录批量加减标签'))
    @action(detail=False, methods=['post'], url_path='quote-bulk-tags')
    def quote_bulk_tags(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ids = request.data.get('ids') or []
        operation = (request.data.get('operation') or 'add').strip()
        if operation not in ('add', 'remove'):
            return ApiResponse(message=_('operation 须为 add 或 remove'), code=400)
        if not ids:
            return ApiResponse(message=_('请先勾选需要批量处理的语录'), code=400)
        adding = operation == 'add'
        theme = (request.data.get('theme') or '').strip()
        taxonomy = request.data.get('taxonomy') or {}
        if not isinstance(taxonomy, dict):
            taxonomy = {}
        display = request.data.get('display_tags') or []
        if not isinstance(display, list):
            display = []
        required_keys = set(
            TagType.objects.filter(required=True).values_list('key', flat=True)
        )
        from tools.interest_theme import normalize_interest_value, sync_interest_tags_from_theme

        qs = Quote.objects.filter(id__in=ids, is_deleted=False)
        n = 0
        for row in qs:
            denied = _deny_app_write(request, row.app_key or '')
            if denied:
                continue
            scenario = list(row.scenario_tags or [])
            emotion = list(row.emotion_tags or [])
            tax = dict(row.taxonomy or {})
            disp = list(row.display_tags or [])
            new_theme = row.theme
            if adding and theme:
                new_theme = normalize_interest_value(theme) or theme
            for key, names in taxonomy.items():
                if key in ('content', 'theme', 'visual'):
                    continue
                names = [x for x in (names or []) if x]
                if not names:
                    continue
                if not adding and key in required_keys:
                    continue
                if key == 'scenario':
                    scenario = list(dict.fromkeys(scenario + names)) if adding else [x for x in scenario if x not in names]
                elif key == 'emotion':
                    emotion = list(dict.fromkeys(emotion + names)) if adding else [x for x in emotion if x not in names]
                else:
                    existing = list(tax.get(key) or [])
                    tax[key] = list(dict.fromkeys(existing + names)) if adding else [x for x in existing if x not in names]
            if display:
                disp = list(dict.fromkeys(disp + display)) if adding else [x for x in disp if x not in display]
            fields = {
                'theme': new_theme,
                'scenario_tags': scenario,
                'emotion_tags': emotion,
                'taxonomy': tax,
                'display_tags': disp,
                'content_type': row.content_type,
            }
            rebuild_quote_index(fields)
            fields['interest_tags'] = sync_interest_tags_from_theme(new_theme, row.interest_tags)
            Quote.objects.filter(pk=row.pk).update(**fields)
            # 自动创建缺失的隐性标签
            if adding:
                ensure_tag_exists('theme', new_theme)
                for _name in scenario:
                    ensure_tag_exists('scenario', _name)
                for _name in emotion:
                    ensure_tag_exists('emotion', _name)
                for _type_key, _names in tax.items():
                    for _name in _names:
                        ensure_tag_exists(_type_key, _name)
            n += 1
        return ApiResponse(data={'updated': n})
