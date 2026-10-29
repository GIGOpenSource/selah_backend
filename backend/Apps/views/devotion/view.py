#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""灵修课包 API。"""
import logging

from django.db import transaction
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import DevotionProgress, DevotionSeries, DevotionSession, MannaContentTag
from tools.manna_biz import (
    evaluate_manna_badges, grant_kind_amen_reward, touch_activity_day, user_local_now,
    REASON_DEVOTION_AMEN,
)
from tools.permissions import IsTokenValid
from tools.selah_quota import (
    MANNA_APP_KEY,
    filter_scoped_rows,
    get_config,
    normalize_app_key,
    normalize_lang,
    normalize_platform,
    set_config_context_from_request,
)
from tools.utils import ApiResponse
from Apps.views.analytics.view import record_content_open

logger = logging.getLogger(__name__)


def _scope(user):
    app = normalize_app_key(getattr(user, 'app_key', '') or '') or MANNA_APP_KEY
    lang = normalize_lang(getattr(user, 'system_lang', None) or 'en')
    plat = normalize_platform(getattr(user, 'platform', None) or '')
    return app, lang, plat


def _vip_gate_on():
    """manna_vip_enabled=false → 不锁 VIP 灵修（隐藏商业化门禁）。"""
    return bool(get_config('manna_vip_enabled', False))


def _locked_for(user, is_vip):
    if not is_vip or not _vip_gate_on():
        return False
    return not getattr(user, 'is_vip_active', False)


def _series_status(done_n, total):
    if done_n <= 0:
        return 'not_started'
    if total and done_n >= total:
        return 'done'
    return 'in_progress'


def _serialize_series_card(r, user, prog_map):
    tags = r.tags if isinstance(r.tags, list) else []
    prog = prog_map.get(r.id)
    done_n = len(prog.completed_session_ids or []) if prog else 0
    total = int(r.session_count or 0)
    bio = r.pastor_bio or ''
    return {
        'id': r.id, 'title': r.title, 'subtitle': bio,
        'cover_url': r.cover_url or '', 'tags': tags,
        'session_count': r.session_count, 'is_vip': r.is_vip,
        'locked': _locked_for(user, r.is_vip),
        'completed_count': done_n,
        'status': _series_status(done_n, total),
    }


def _preview_sessions(series_id, prog_map, limit=2):
    prog = prog_map.get(series_id)
    done = set(prog.completed_session_ids or []) if prog else set()
    sessions = list(
        DevotionSession.objects.filter(series_id=series_id, enabled=True)
        .order_by('sort_order', 'id')[:limit]
    )
    return [{
        'id': s.id,
        'title': s.title,
        'scripture_ref': s.scripture_ref or '',
        'completed': s.id in done,
    } for s in sessions]


def _resolve_tag_row(app, lang, plat, tag_key):
    """按 Tag.key 解析（大小写不敏感）；不认 display_name 作为 query。"""
    if not tag_key:
        return None
    rows = filter_scoped_rows(
        list(
            MannaContentTag.objects.filter(enabled=True, app_key=app).filter(
                key__iexact=tag_key,
            )
        ),
        lang, plat, group_key='key', app_key=app,
    )
    return rows[0] if rows else None


def _series_matches_tag_key(series_tags, tag_row, tag_key):
    """系列 tags 可能存 key 或历史 display_name；query 一律按 key。"""
    if not tag_key:
        return True
    aliases = {str(tag_key).strip().lower()}
    if tag_row is not None:
        aliases.add(str(tag_row.key or '').strip().lower())
        aliases.add(str(tag_row.display_name or '').strip().lower())
    aliases.discard('')
    for raw in (series_tags or []):
        if str(raw or '').strip().lower() in aliases:
            return True
    return False


def _configured_featured_series_id(app, lang, plat, tag_key):
    """按 Tag.key 取运营配置的推荐系列 ID。"""
    row = _resolve_tag_row(app, lang, plat, tag_key)
    if not row:
        return None
    fid = getattr(row, 'featured_series_id', None)
    try:
        fid = int(fid or 0)
    except (TypeError, ValueError):
        fid = 0
    return fid or None


def _pick_featured(cards, preferred_id=None):
    """优先运营配置且未学过；否则第一个 not_started；都没有则 null。"""
    by_id = {c['id']: c for c in cards}
    if preferred_id and preferred_id in by_id:
        preferred = by_id[preferred_id]
        if preferred.get('status') == 'not_started':
            return preferred
    for c in cards:
        if c.get('status') == 'not_started':
            return c
    return None


@extend_schema(tags=[_('Devotion')])
class DevotionViewSet(ViewSet):
    permission_classes = [IsTokenValid]

    @extend_schema(summary=_('系列列表（含推荐）'))
    @action(detail=False, methods=['get'], url_path='series')
    def series(self, request):
        """按 tag(=ContentTag.key) 返回 featured + list；推荐由后端选定。

        - tag：传 /api/bible/tags/ 的 key，不是 display_name
        - featured：运营配置优先（未学过），否则同列表第一个 not_started；无则 null
        - featured.preview_sessions：推荐包前 2 节（标题+经文出处）
        - list：其余系列；进行中排前
        - mine=1：不返回推荐位（个人列表）
        """
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        tag = (request.query_params.get('tag') or '').strip()
        mine = str(request.query_params.get('mine') or '').strip().lower() in (
            '1', 'true', 'yes',
        )
        tag_row = _resolve_tag_row(app, lang, plat, tag) if tag else None
        rows = filter_scoped_rows(
            list(DevotionSeries.objects.filter(enabled=True).order_by('sort_order', 'id')),
            lang, plat, app_key=app,
        )
        prog_map = {
            r.series_id: r
            for r in DevotionProgress.objects.filter(user_id=request.user.id, app_key=app)
        }
        data = []
        for r in rows:
            tags = r.tags if isinstance(r.tags, list) else []
            if tag and not _series_matches_tag_key(tags, tag_row, tag):
                continue
            data.append(_serialize_series_card(r, request.user, prog_map))

        if mine:
            data = [x for x in data if x['status'] in ('in_progress', 'done')]
            data.sort(key=lambda x: 0 if x['status'] == 'in_progress' else 1)
            return ApiResponse(data={'featured': None, 'list': data})

        preferred_id = _configured_featured_series_id(app, lang, plat, tag) if tag else None
        featured = _pick_featured(data, preferred_id)
        if featured:
            featured = dict(featured)
            featured['preview_sessions'] = _preview_sessions(featured['id'], prog_map, limit=2)
            rest = [x for x in data if x['id'] != featured['id']]
        else:
            rest = list(data)
        in_progress = [x for x in rest if x['status'] == 'in_progress']
        others = [x for x in rest if x['status'] != 'in_progress']
        return ApiResponse(data={
            'featured': featured,
            'list': in_progress + others,
        })

    @extend_schema(summary=_('系列详情'))
    @action(detail=False, methods=['get'], url_path='series-detail')
    def series_detail(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user)
        try:
            sid = int(request.query_params.get('id') or 0)
        except (TypeError, ValueError):
            sid = 0
        series = DevotionSeries.objects.filter(id=sid, enabled=True).first()
        if not series:
            return ApiResponse(message='not found', code=404)
        sessions = list(
            DevotionSession.objects.filter(series_id=series.id, enabled=True)
            .order_by('sort_order', 'id')
        )
        prog = DevotionProgress.objects.filter(
            user_id=request.user.id, app_key=app, series_id=series.id,
        ).first()
        done = set(prog.completed_session_ids or []) if prog else set()
        record_content_open(
            request.user, 'devotion_series', series.id,
            title=series.title or '', app_key=app,
        )
        return ApiResponse(data={
            'id': series.id,
            'title': series.title,
            'subtitle': series.pastor_bio or '',
            'cover_url': series.cover_url or '',
            'pastor_name': series.pastor_name or '',
            'pastor_avatar_url': series.pastor_avatar_url or '',
            'tags': series.tags or [],
            'session_count': series.session_count,
            'is_vip': series.is_vip,
            'locked': _locked_for(request.user, series.is_vip),
            'completed_session_ids': list(done),
            'sessions': [{
                'id': s.id, 'title': s.title, 'scripture_ref': s.scripture_ref or '',
                'sort_order': s.sort_order, 'duration_hint_sec': s.duration_hint_sec,
                'completed': s.id in done,
            } for s in sessions],
        })

    @extend_schema(summary=_('课时正文'))
    @action(detail=False, methods=['get'], url_path='session')
    def session(self, request):
        set_config_context_from_request(request)
        try:
            sid = int(request.query_params.get('id') or 0)
        except (TypeError, ValueError):
            sid = 0
        s = DevotionSession.objects.filter(id=sid, enabled=True).first()
        if not s:
            return ApiResponse(message='not found', code=404)
        series = DevotionSeries.objects.filter(id=s.series_id, enabled=True).first()
        sibling_ids = list(
            DevotionSession.objects.filter(series_id=s.series_id, enabled=True)
            .order_by('sort_order', 'id')
            .values_list('id', flat=True)
        )
        try:
            session_index = sibling_ids.index(s.id) + 1
        except ValueError:
            session_index = 0
        app, _, _ = _scope(request.user)
        record_content_open(
            request.user, 'devotion_session', s.id,
            title=s.title or '', app_key=app, series_id=s.series_id,
        )
        return ApiResponse(data={
            'id': s.id, 'series_id': s.series_id, 'title': s.title,
            'scripture_ref': s.scripture_ref or '',
            'scripture_text': s.scripture_text or '',
            'body': s.body,
            'duration_hint_sec': s.duration_hint_sec, 'sort_order': s.sort_order,
            'series_title': (series.title if series else '') or '',
            'cover_url': (series.cover_url if series else '') or '',
            'session_count': int(series.session_count or 0) if series else len(sibling_ids),
            'session_index': session_index,
        })

    @extend_schema(summary=_('用户进度'))
    @action(detail=False, methods=['get'], url_path='progress')
    def progress(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user)
        rows = DevotionProgress.objects.filter(user_id=request.user.id, app_key=app)
        return ApiResponse(data=[{
            'series_id': r.series_id,
            'completed_session_ids': r.completed_session_ids or [],
            'updated_at': r.updated_at.isoformat() if r.updated_at else None,
        } for r in rows])

    @extend_schema(summary=_('完成课时'))
    @action(detail=False, methods=['post'], url_path='complete')
    def complete(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        try:
            session_id = int(request.data.get('session_id') or 0)
        except (TypeError, ValueError):
            return ApiResponse(message='session_id required', code=400)
        s = DevotionSession.objects.filter(id=session_id, enabled=True).first()
        if not s:
            return ApiResponse(message='not found', code=404)
        series = DevotionSeries.objects.filter(id=s.series_id, enabled=True).first()
        if series and _locked_for(request.user, series.is_vip):
            return ApiResponse(message='vip required', code=403)
        first_complete = False
        with transaction.atomic():
            prog, _ = DevotionProgress.objects.get_or_create(
                user_id=request.user.id, app_key=app, series_id=s.series_id,
                defaults={'completed_session_ids': []},
            )
            prog = DevotionProgress.objects.select_for_update().get(pk=prog.pk)
            done = list(prog.completed_session_ids or [])
            first_complete = session_id not in done
            if first_complete:
                done.append(session_id)
                prog.completed_session_ids = done
                prog.save(update_fields=['completed_session_ids', 'updated_at'])
        sec = int(request.data.get('seconds') or s.duration_hint_sec or 0)
        local_dt, _tz = user_local_now(request.user, request.data.get('tz'))
        local_date = local_dt.date()
        touch_activity_day(
            request.user, app, day=local_date,
            devotion_sec=max(0, sec), flags={'devotion': True},
        )
        grant = {'manna_delta': 0, 'balance': 0, 'already': True}
        if first_complete:
            grant = grant_kind_amen_reward(
                request.user, app, str(session_id),
                'manna_devotion_amen_reward', REASON_DEVOTION_AMEN, 'devotion_amen_ids',
                local_date=local_date.isoformat(),
            )
        newly = evaluate_manna_badges(request.user, app, language=lang, platform=plat)
        return ApiResponse(data={
            'series_id': s.series_id,
            'completed_session_ids': prog.completed_session_ids,
            'manna_delta': grant['manna_delta'],
            'balance': grant['balance'],
            'seconds': max(0, sec),
            'newly_earned': newly,
        })
