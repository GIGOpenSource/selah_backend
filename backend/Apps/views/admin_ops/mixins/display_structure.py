#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""AdminOps mixin — 显性结构编辑态、预览、统一发布。"""
from django.db.models import Max
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from Apps.views.admin_ops._helpers import (  # noqa: F401
    DisplayTag, DisplayStructureState, Tag, IdSerializer, PERM_CONTENT, has_perm,
    ApiResponse,
)
from tools.display_structure import (
    discard_structure, get_structure_state, mark_structure_dirty, preview_matches,
    publish_structure, serialize_structure, sync_wallpaper_filters,
)
from tools.tag_library import ensure_default_tag_library, serialize_display_tag


def _parse_kind(raw):
    kind = (raw or DisplayTag.KIND_QUOTE).strip()
    if kind not in (DisplayTag.KIND_QUOTE, DisplayTag.KIND_WALLPAPER):
        return None
    return kind


class DisplayStructureMixin:
    @extend_schema(summary=_('显性结构（编辑态 + 已发布）'))
    @action(detail=False, methods=['get'], url_path='display-structure')
    def display_structure(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ensure_default_tag_library()
        kind = _parse_kind(request.query_params.get('kind'))
        if not kind:
            return ApiResponse(message=_('kind 须为 quote 或 wallpaper'), code=400)
        return ApiResponse(data=serialize_structure(kind))

    @extend_schema(summary=_('收录结果预览'))
    @action(detail=False, methods=['post'], url_path='display-preview')
    def display_preview(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        kind = _parse_kind(request.data.get('kind'))
        if not kind:
            return ApiResponse(message=_('kind 须为 quote 或 wallpaper'), code=400)
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
        return ApiResponse(data=preview_matches(kind, mode, match, conditions, manual_ids))

    @extend_schema(summary=_('壁纸筛选标签前端展示开关'))
    @action(detail=False, methods=['post'], url_path='wallpaper-filter-display')
    def wallpaper_filter_display(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        tag_id = request.data.get('tag_id') or request.data.get('id')
        try:
            tag_id = int(tag_id)
        except (TypeError, ValueError):
            return ApiResponse(message=_('请指定隐性标签'), code=400)
        if not Tag.objects.filter(id=tag_id).exists():
            return ApiResponse(message='not found', code=404)
        state = sync_wallpaper_filters()
        draft = dict(state.wallpaper_filters or {})
        enabled = request.data.get('display_enabled')
        if enabled is None:
            enabled = not bool(draft.get(str(tag_id)))
        draft[str(tag_id)] = bool(enabled)
        DisplayStructureState.objects.filter(pk=state.pk).update(wallpaper_filters=draft, dirty=True)
        publish_structure(DisplayTag.KIND_WALLPAPER, operator=getattr(request.user, 'username', '') or '')
        return ApiResponse(data={'tag_id': tag_id, 'display_enabled': bool(enabled)})

    @extend_schema(summary=_('显性结构确认发布'))
    @action(detail=False, methods=['post'], url_path='display-structure-publish')
    def display_structure_publish(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        kind = _parse_kind(request.data.get('kind'))
        if not kind:
            return ApiResponse(message=_('kind 须为 quote 或 wallpaper'), code=400)
        state = get_structure_state(kind)
        if not state.dirty and state.published_version:
            return ApiResponse(message=_('当前结构已发布'), code=400)
        operator = getattr(request.user, 'username', '') or ''
        publish_structure(kind, operator=operator)
        return ApiResponse(data=serialize_structure(kind), message=_('结构发布成功'))

    @extend_schema(summary=_('显性结构取消未发布调整'))
    @action(detail=False, methods=['post'], url_path='display-structure-discard')
    def display_structure_discard(self, request):
        """放弃编辑态，恢复为上次已发布结构。语录、壁纸都走这个接口，用 kind 区分。"""
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        kind = _parse_kind(request.data.get('kind'))
        if not kind:
            return ApiResponse(message=_('kind 须为 quote 或 wallpaper'), code=400)
        state = get_structure_state(kind)
        if not state.dirty:
            return ApiResponse(message=_('当前没有未发布的调整'), code=400)
        discard_structure(kind)
        return ApiResponse(data=serialize_structure(kind), message=_('已恢复为上次发布的结构'))

    @extend_schema(summary=_('调整显性结构排序或所属'))
    @action(detail=False, methods=['post'], url_path='display-structure-move')
    def display_structure_move(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        ser = IdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        row = DisplayTag.objects.filter(id=ser.validated_data['id']).first()
        if not row:
            return ApiResponse(message='not found', code=404)
        parent_id = request.data.get('parent_id', '__keep__')
        direction = int(request.data.get('direction') or 0)
        if parent_id != '__keep__' and row.display_type == DisplayTag.TYPE_COLLECTION:
            try:
                parent_id = int(parent_id) if parent_id not in (None, '') else None
            except (TypeError, ValueError):
                return ApiResponse(message=_('所属文字模块不合法'), code=400)
            parent = DisplayTag.objects.filter(
                id=parent_id, kind=row.kind, display_type=DisplayTag.TYPE_COLUMN, status=1,
            ).first() if parent_id else None
            if not parent:
                return ApiResponse(message=_('只能移动到已展示的文字模块'), code=400)
            source = DisplayTag.objects.filter(id=row.parent_id).first()
            if source and source.status != 1:
                return ApiResponse(message=_('该文字模块未展示，不能调整其文字合集标题'), code=400)
            siblings = DisplayTag.objects.filter(
                kind=row.kind, display_type=row.display_type, parent_id=parent.id,
            ).exclude(pk=row.pk)
            next_sort = (siblings.aggregate(m=Max('sort')).get('m') or 0) + 1
            DisplayTag.objects.filter(pk=row.pk).update(parent_id=parent.id, sort=next_sort)
            mark_structure_dirty(row.kind)
            return ApiResponse(data=serialize_structure(row.kind))
        if direction not in (-1, 1):
            return ApiResponse(message=_('direction 须为 -1 或 1'), code=400)
        if row.display_type == DisplayTag.TYPE_COLLECTION:
            parent = DisplayTag.objects.filter(id=row.parent_id).first()
            if parent and parent.status != 1:
                return ApiResponse(message=_('该文字模块未展示，不能调整其文字合集标题'), code=400)
        siblings = list(DisplayTag.objects.filter(
            kind=row.kind, display_type=row.display_type, parent_id=row.parent_id,
        ).order_by('sort', 'id'))
        index = next((i for i, item in enumerate(siblings) if item.id == row.id), -1)
        target = siblings[index + direction] if 0 <= index + direction < len(siblings) else None
        if not target:
            return ApiResponse(data=serialize_structure(row.kind))
        DisplayTag.objects.filter(pk=row.pk).update(sort=target.sort)
        DisplayTag.objects.filter(pk=target.pk).update(sort=row.sort)
        mark_structure_dirty(row.kind)
        return ApiResponse(data=serialize_structure(row.kind))
