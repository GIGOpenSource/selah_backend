#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Manna 经济与成长 API（Phase A–E + 2.0 早鸟：喜欢/Home卡/成长卡）。"""
import calendar
import logging
from datetime import date, timedelta

from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import (
    BibleAmen, BibleHighlight, BibleNote,
    MannaFavorite, MannaHomeCard, MannaLedger, MannaQuizStory,
    MannaQuizStoryUnlock, MannaStatsCopy, MannaTestimony,
    ActivityDay,
)
from tools.manna_biz import (
    TASK_EARN_REASONS, TODAY_EARN_REASONS,
    annotate_story_progress, count_manna_ledger_reason, day_is_active,
    ensure_manna_wallet, evaluate_manna_badges, get_prayer_reminder,
    grant_kind_amen_reward, list_manna_badges, local_day_utc_range,
    pick_daily_verse, resolve_prayer_period,
    serialize_daily_verse, serialize_prayer_from_daily_verse,
    sum_manna_ledger, sum_manna_ledger_by_reason,
    touch_activity_day, user_local_now, versecard_themes_payload,
    REASON_BIBLE_READ, REASON_DEVOTION_AMEN, REASON_PRAYER_AMEN,
    REASON_QUIZ_PASS,
)
from tools.permissions import IsTokenValid
from tools.selah_quota import (
    MANNA_APP_KEY,
    filter_scoped_rows,
    get_config,
    get_manna_wallet_snapshot,
    normalize_app_key,
    normalize_lang,
    normalize_platform,
    set_config_context_from_request,
)
from tools.utils import ApiResponse, CustomPagination
from Apps.views.analytics.view import record_content_open

logger = logging.getLogger(__name__)


def _serialize_favorite(f):
    ttype = f.target_type or ''
    tkey = f.target_key or ''
    quote = ''
    ref = ''
    book_key = ''
    chapter = 0
    verse = 0
    if '|' in tkey:
        ref, quote = tkey.split('|', 1)
    elif ttype in ('bible_bookmark', 'bible_verse'):
        parts = tkey.split(':')
        if len(parts) >= 4:
            book_key = parts[1]
            try:
                chapter = int(parts[2])
                verse = int(parts[3])
            except (TypeError, ValueError):
                chapter = verse = 0
            ref = '%s %s:%s' % (book_key, chapter, verse)
        else:
            ref = tkey
    else:
        ref = tkey
        quote = tkey
    return {
        'id': f.id,
        'target_type': ttype,
        'target_key': tkey,
        'text': quote,
        'verse_text': quote,
        'reference': ref,
        'book_key': book_key,
        'chapter': chapter,
        'verse': verse,
        'created_at': f.created_at.isoformat() if f.created_at else None,
    }


def _scope(user, request=None):
    """Content language follows the App UI (Accept-Language / ?language=), not only stored system_lang."""
    app = normalize_app_key(getattr(user, 'app_key', '') or '') or MANNA_APP_KEY
    lang = normalize_lang(getattr(user, 'system_lang', None) or 'en')
    plat = normalize_platform(getattr(user, 'platform', None) or '')
    if request is None:
        return app, lang, plat
    headers = getattr(request, 'headers', None) or {}
    qp = getattr(request, 'query_params', None)
    q_lang = ''
    q_plat = ''
    if qp is not None:
        q_lang = (qp.get('language') or '').strip()
        q_plat = (qp.get('platform') or '').strip()
    h_lang = ((headers.get('Accept-Language') or '').split(',')[0] or '').strip()
    h_plat = (headers.get('X-Platform') or headers.get('platform') or '').strip()
    h_app = (headers.get('X-App-Key') or headers.get('app-key') or '').strip()
    lang = normalize_lang(q_lang or h_lang or lang)
    plat = normalize_platform(q_plat or h_plat or plat)
    if h_app:
        app = normalize_app_key(h_app) or app
    return app, lang, plat


@extend_schema(tags=[_('Manna')])
class MannaViewSet(ViewSet):
    """Manna 钱包与成长（与 Selah AI quota 隔离）。"""

    permission_classes = [IsTokenValid]

    def get_throttle_scope(self):
        if getattr(self, 'action', None) == 'activity_ping':
            return 'activity'
        return 'user'

    @extend_schema(summary=_('Manna 余额'))
    @action(detail=False, methods=['get'], url_path='balance')
    def balance(self, request):
        set_config_context_from_request(request)
        user = request.user
        app, _, _ = _scope(user, request)
        ensure_manna_wallet(user, app)
        snap = get_manna_wallet_snapshot(user, app_key=app)
        return ApiResponse(data={
            'balance': snap['balance'],
            'stats': snap['stats'],
            'app_key': app,
        })

    @extend_schema(summary=_('今日经文'))
    @action(detail=False, methods=['get'], url_path='daily-verse')
    def daily_verse(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        # tz：IANA；前端应传，缺省回退 user.timezone
        prayer = get_prayer_reminder(
            request.user, app, tz=request.query_params.get('tz'),
        )
        row = pick_daily_verse(app, lang, plat)
        if not row:
            return ApiResponse(data=prayer, message='empty')
        data = serialize_daily_verse(row)
        data.update(prayer)
        record_content_open(
            request.user, 'daily_verse', row.id,
            title=row.reference or '', app_key=app,
        )
        return ApiResponse(data=data)

    @extend_schema(summary=_('Amen'))
    @action(detail=False, methods=['post'], url_path='amen')
    def amen(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        scene = str(request.data.get('scene') or '')[:64]
        ref = str(request.data.get('ref') or '')[:255]
        BibleAmen.objects.create(
            user_id=request.user.id, app_key=app, scene=scene, ref=ref,
        )
        # 首页 Amen 不记功课日，避免日历误标「已使用」
        newly = evaluate_manna_badges(request.user, app, language=lang, platform=plat)
        return ApiResponse(data={'ok': True, 'newly_earned': newly}, code=201)

    @extend_schema(summary=_('四维统计'))
    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user, request)
        ensure_manna_wallet(request.user, app)
        snap = get_manna_wallet_snapshot(request.user, app_key=app)
        pray_count = count_manna_ledger_reason(request.user, app, REASON_PRAYER_AMEN)
        devotion_count = count_manna_ledger_reason(request.user, app, REASON_DEVOTION_AMEN)
        return ApiResponse(data={
            'balance': snap['balance'],
            'stats': snap['stats'],
            'pray_sec': int((snap['stats'] or {}).get('pray_sec') or 0),
            'devotion_sec': int((snap['stats'] or {}).get('devotion_sec') or 0),
            'read_sec': int((snap['stats'] or {}).get('read_sec') or 0),
            'quiz_count': int((snap['stats'] or {}).get('quiz_count') or 0),
            # 与流水同步：祷告/灵修按 Amen 完成次数
            'pray_count': pray_count,
            'devotion_count': devotion_count,
        })

    @extend_schema(summary=_('活动日历'))
    @action(detail=False, methods=['get'], url_path='calendar')
    def calendar(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user, request)
        local_dt, _tz = user_local_now(request.user, request.query_params.get('tz'))
        try:
            year = int(request.query_params.get('year') or local_dt.year)
            month = int(request.query_params.get('month') or local_dt.month)
        except (TypeError, ValueError):
            return ApiResponse(message='invalid year/month', code=400)
        days_in = calendar.monthrange(year, month)[1]
        start = date(year, month, 1)
        end = date(year, month, days_in)
        rows = {
            r.date.isoformat(): r
            for r in ActivityDay.objects.filter(
                user_id=request.user.id, app_key=app, date__gte=start, date__lte=end,
            )
        }
        out = []
        for d in range(1, days_in + 1):
            key = date(year, month, d).isoformat()
            row = rows.get(key)
            active = day_is_active(row)
            out.append({
                'date': key,
                'active': active,
                'flags': (row.flags if row else {}) or {},
                'pray_sec': int(row.pray_sec) if row else 0,
                'devotion_sec': int(row.devotion_sec) if row else 0,
                'read_sec': int(row.read_sec) if row else 0,
                'quiz_count': int(row.quiz_count) if row else 0,
            })
        return ApiResponse(data={'year': year, 'month': month, 'days': out})

    @extend_schema(summary=_('日历日摘要'))
    @action(detail=False, methods=['get'], url_path='calendar-day')
    def calendar_day(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user, request)
        local_dt, _tz = user_local_now(request.user, request.query_params.get('tz'))
        raw = (request.query_params.get('date') or '').strip()
        try:
            day = date.fromisoformat(raw) if raw else local_dt.date()
        except ValueError:
            return ApiResponse(message='invalid date', code=400)
        row = ActivityDay.objects.filter(
            user_id=request.user.id, app_key=app, date=day,
        ).first()
        return ApiResponse(data={
            'date': day.isoformat(),
            'active': day_is_active(row),
            'flags': (row.flags if row else {}) or {},
            'pray_sec': int(row.pray_sec) if row else 0,
            'devotion_sec': int(row.devotion_sec) if row else 0,
            'read_sec': int(row.read_sec) if row else 0,
            'quiz_count': int(row.quiz_count) if row else 0,
        })

    @extend_schema(summary=_('活动上报'))
    @action(detail=False, methods=['post'], url_path='activity-ping')
    def activity_ping(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user, request)
        local_dt, _tz = user_local_now(request.user, request.data.get('tz'))
        scene = str(request.data.get('scene') or '')
        try:
            sec = int(request.data.get('seconds') or request.data.get('sec') or 0)
        except (TypeError, ValueError):
            sec = 0
        sec = min(3600, max(0, sec))
        kwargs = {}
        if scene in ('pray', 'prayer'):
            if sec <= 0:
                return ApiResponse(data={'ok': True, 'skipped': True})
            kwargs = {'pray_sec': sec, 'flags': {'prayer': True, scene: True}}
        elif scene == 'devotion':
            if sec <= 0:
                return ApiResponse(data={'ok': True, 'skipped': True})
            kwargs = {'devotion_sec': sec, 'flags': {'devotion': True}}
        elif scene in ('read', 'bible'):
            if sec <= 0:
                return ApiResponse(data={'ok': True, 'skipped': True})
            kwargs = {'read_sec': sec}
            if sec >= 60:
                kwargs['flags'] = {'read': True, 'bible': True}
        elif scene == 'quiz':
            try:
                count = int(request.data.get('count') or 1)
            except (TypeError, ValueError):
                count = 1
            kwargs = {
                'quiz_count': min(5, max(1, count)),
                'flags': {'quiz': True},
            }
        else:
            return ApiResponse(data={'ok': True, 'skipped': True})
        touch_activity_day(request.user, app, day=local_dt.date(), **kwargs)
        return ApiResponse(data={'ok': True})

    @extend_schema(summary=_('徽章'))
    @action(detail=False, methods=['get'], url_path='badges')
    def badges(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        data = list_manna_badges(request.user, app, language=lang, platform=plat)
        return ApiResponse(data=data)

    @extend_schema(summary=_('祷告内容'))
    @action(detail=False, methods=['get'], url_path='prayer')
    def prayer(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        kind = (request.query_params.get('kind') or '').strip()
        row = pick_daily_verse(app, lang, plat)
        data = serialize_prayer_from_daily_verse(row, kind)
        if data and row:
            record_content_open(
                request.user, 'prayer', row.id,
                title=data.get('title') or row.reference or '',
                app_key=app, kind=data.get('kind') or kind or 'morning',
            )
        return ApiResponse(data=[data] if data else [])

    @extend_schema(summary=_('祷告 Amen'))
    @action(detail=False, methods=['post'], url_path='prayer/amen')
    def prayer_amen(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        try:
            sec = int(request.data.get('seconds') or 0)
        except (TypeError, ValueError):
            sec = 0
        # 时段与业务日以服务端 + 请求 tz 为准（勿信客户端 local_date 刷日）
        local_dt, _tz_name = user_local_now(request.user, request.data.get('tz'))
        kind, business_date = resolve_prayer_period(local_dt)
        BibleAmen.objects.create(
            user_id=request.user.id, app_key=app, scene=f'prayer:{kind}',
            ref=str(request.data.get('ref') or '')[:255],
        )
        flags = {'prayer': True, kind: True}
        touch_activity_day(
            request.user, app, day=business_date,
            pray_sec=max(0, sec), flags=flags,
        )
        grant = grant_kind_amen_reward(
            request.user, app, kind,
            'manna_prayer_amen_reward', REASON_PRAYER_AMEN, 'prayer_amen_dates',
            local_date=business_date.isoformat(),
        )
        newly = evaluate_manna_badges(request.user, app, language=lang, platform=plat)
        prayer = get_prayer_reminder(
            request.user, app, tz=request.data.get('tz'), persist_tz=False,
        )
        data = {
            'ok': True,
            'kind': kind,
            'business_date': business_date.isoformat(),
            'manna_delta': grant['manna_delta'],
            'balance': grant['balance'],
            'already': grant['already'],
            'newly_earned': newly,
        }
        data.update(prayer)
        return ApiResponse(data=data, code=201)

    @extend_schema(summary=_('收藏聚合'))
    @action(detail=False, methods=['get'], url_path='collections')
    def collections(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user, request)
        uid = request.user.id
        highlights = list(
            BibleHighlight.objects.filter(user_id=uid, app_key=app).order_by('-id')[:100]
        )
        notes = list(BibleNote.objects.filter(user_id=uid, app_key=app).order_by('-id')[:100])
        favs = list(MannaFavorite.objects.filter(user_id=uid, app_key=app).order_by('-id')[:100])
        return ApiResponse(data={
            'highlights': [{
                'id': h.id, 'book_key': h.book_key, 'chapter': h.chapter, 'verse': h.verse,
                'color': h.color, 'translation_code': h.translation_code,
            } for h in highlights],
            'notes': [{
                'id': n.id, 'book_key': n.book_key, 'chapter': n.chapter, 'verse': n.verse,
                'body': n.body, 'translation_code': n.translation_code,
            } for n in notes],
            'favorites': [_serialize_favorite(f) for f in favs],
        })

    @extend_schema(summary=_('已解锁故事卡'))
    @action(detail=False, methods=['get'], url_path='story-cards')
    def story_cards(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        unlock_ids = set(
            MannaQuizStoryUnlock.objects.filter(
                user_id=request.user.id, app_key=app,
            ).values_list('story_id', flat=True)
        )
        rows = filter_scoped_rows(
            list(MannaQuizStory.objects.filter(enabled=True, app_key=app).order_by('sort_order', 'id')),
            lang, plat, app_key=app,
        )
        progress = annotate_story_progress(rows, unlock_ids)
        return ApiResponse(data=[{
            'id': s.id, 'title': s.title, 'cover_url': s.cover_url or '',
            'card_url': s.card_url or '',
            'unlock_cost': 0,
            'unlocked': progress.get(s.id, {}).get('unlocked', False),
            'available': progress.get(s.id, {}).get('available', False),
            'locked': progress.get(s.id, {}).get('locked', True),
        } for s in rows])

    @extend_schema(summary=_('Manna 流水'))
    @action(detail=False, methods=['get'], url_path='ledger')
    def ledger(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user, request)
        qs = MannaLedger.objects.filter(
            user_id=request.user.id, app_key=app,
        ).order_by('-created_at', '-id')
        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response([{
            'id': r.id, 'delta': r.delta, 'balance_after': r.balance_after,
            'reason': r.reason, 'ref_type': r.ref_type, 'ref_id': r.ref_id,
            'remark': r.remark,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        } for r in page])

    @extend_schema(summary=_('喜欢切换'))
    @action(detail=False, methods=['post'], url_path='favorite/toggle')
    def favorite_toggle(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user, request)
        ttype = str(request.data.get('target_type') or '').strip()
        tkey = str(request.data.get('target_key') or '').strip()
        if not ttype or not tkey:
            return ApiResponse(message='target_type/target_key required', code=400)
        existing = MannaFavorite.objects.filter(
            user_id=request.user.id, app_key=app, target_type=ttype, target_key=tkey,
        ).first()
        if existing:
            existing.delete()
            return ApiResponse(data={'liked': False})
        MannaFavorite.objects.create(
            user_id=request.user.id, app_key=app, target_type=ttype, target_key=tkey,
        )
        return ApiResponse(data={'liked': True}, code=201)

    @extend_schema(summary=_('分享载荷'))
    @action(detail=False, methods=['get'], url_path='share-payload')
    def share_payload(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        scene = (request.query_params.get('scene') or 'daily_verse').strip()
        ref = (request.query_params.get('ref') or '').strip()
        text = (request.query_params.get('text') or '').strip()
        reference = ref
        themes = versecard_themes_payload(
            app, lang, plat,
            bg=request.query_params.get('bg') or '',
            theme_key=request.query_params.get('theme_key') or '',
        )
        # 徽章分享：只用标题+日期+MANNA，不回落今日经文、不带下载/商店链
        if scene == 'badge':
            brand = 'MANNA'
            bits = [p for p in (text, reference) if p]
            share_text = (' — '.join(bits) + ' · ' + brand) if bits else brand
            return ApiResponse(data={
                'text': share_text,
                'brand': brand,
                'raw_text': text,
                'reference': reference,
                'href': '',
                **themes,
            })
        # home / daily_verse 等场景：未带正文时回退今日经文，避免分享卡空白
        if not text:
            row = pick_daily_verse(app, lang, plat)
            if row:
                text = row.verse_text or ''
                reference = row.reference or reference
        brand = str(get_config('versecard_brand', 'MANNA') or 'MANNA')
        tpl = str(get_config('share_text_template', '{text} — {reference} · {brand}') or '')
        try:
            share_text = tpl.format(text=text, reference=reference, brand=brand)
        except Exception:
            share_text = f'{text} — {reference} · {brand}'
        return ApiResponse(data={
            'text': share_text, 'brand': brand, 'raw_text': text, 'reference': reference,
            **themes,
        })

    @extend_schema(summary=_('Verse card 主题列表'))
    @action(detail=False, methods=['get'], url_path='versecard-themes')
    def versecard_themes(self, request):
        """底栏主题图：只返回上架行；同图 URL 去重；用 theme_key 选中，禁止客户端写死六张。"""
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        data = versecard_themes_payload(
            app, lang, plat,
            bg=request.query_params.get('bg') or '',
            theme_key=request.query_params.get('theme_key') or '',
        )
        return ApiResponse(data=data)

    @extend_schema(summary=_('Home 运营卡'))
    @action(detail=False, methods=['get'], url_path='home/cards')
    def home_cards(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        local_dt, _tz = user_local_now(request.user, request.query_params.get('tz'))
        today = local_dt.date()
        rows = list(
            MannaHomeCard.objects.filter(enabled=True, app_key=app).order_by('sort_order', 'id')
        )
        scoped = filter_scoped_rows(rows, lang, plat, app_key=app)
        out = []
        for r in scoped:
            if r.active_date and r.active_date != today:
                continue
            out.append({
                'id': r.id, 'card_type': r.card_type or '', 'title': r.title,
                'subtitle': r.subtitle or '', 'cover_url': r.cover_url or '',
                'link_path': r.link_path or '',
                'payload': r.payload or {}, 'sort_order': r.sort_order,
                'active_date': r.active_date.isoformat() if r.active_date else None,
            })
        return ApiResponse(data=out)

    @extend_schema(summary=_('成长卡'))
    @action(detail=False, methods=['get'], url_path='stats/card')
    def stats_card(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        ensure_manna_wallet(request.user, app)
        snap = get_manna_wallet_snapshot(request.user, app_key=app)
        copies = filter_scoped_rows(
            list(MannaStatsCopy.objects.filter(enabled=True).order_by('sort_order', 'id')),
            lang, plat, app_key=app,
        )
        line = copies[0].line if copies else ''
        local_dt, tz_name = user_local_now(request.user, request.query_params.get('tz'))
        today = local_dt.date()
        # ISO weekday Mon=0（用户本地周）
        start = today - timedelta(days=today.weekday())
        week = []
        for i in range(7):
            d = start + timedelta(days=i)
            row = ActivityDay.objects.filter(
                user_id=request.user.id, app_key=app, date=d,
            ).first()
            flags = (row.flags if row and isinstance(row.flags, dict) else {}) or {}
            prayer_on = bool(
                flags.get('prayer') or flags.get('morning') or flags.get('night')
                or (row and row.pray_sec)
            )
            devotion_on = bool(flags.get('devotion') or (row and row.devotion_sec))
            read_on = bool(flags.get('read') or flags.get('bible') or (row and row.read_sec))
            quiz_on = bool(flags.get('quiz') or (row and row.quiz_count))
            active = bool(prayer_on or devotion_on or read_on or quiz_on)
            week.append({
                'dow': i, 'date': d.isoformat(), 'active': active,
                'prayer': prayer_on, 'devotion': devotion_on,
                'read': read_on, 'quiz': quiz_on,
            })
        # 四格：各行为终身获得 Manna（字段名兼容旧 H5）
        earned = sum_manna_ledger_by_reason(request.user, app, TASK_EARN_REASONS)
        pray_m = int(earned.get(REASON_PRAYER_AMEN) or 0)
        devotion_m = int(earned.get(REASON_DEVOTION_AMEN) or 0)
        read_m = int(earned.get(REASON_BIBLE_READ) or 0)
        quiz_m = int(earned.get(REASON_QUIZ_PASS) or 0)
        day_start, day_end = local_day_utc_range(today, tz_name)
        manna_today = sum_manna_ledger(
            request.user, app, TODAY_EARN_REASONS,
            created_gte=day_start, created_lt=day_end,
        )
        return ApiResponse(data={
            'line': line,
            'week': week,
            'manna': snap['balance'],
            'manna_today': manna_today,
            # 兼容现有字段名；语义改为各行为累计获得的 Manna
            'pray_min': pray_m,
            'devotion_min': devotion_m,
            'pray_num': pray_m,
            'devotion_num': devotion_m,
            'read_min': read_m,
            'quiz_num': quiz_m,
        })

    @extend_schema(summary=_('Testimony 列表'))
    @action(detail=False, methods=['get'], url_path='testimonies')
    def testimonies(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user, request)
        tid = request.query_params.get('id')
        if tid:
            row = MannaTestimony.objects.filter(id=tid, enabled=True).first()
            if not row:
                return ApiResponse(message='not found', code=404)
            return ApiResponse(data={
                'id': row.id, 'title': row.title, 'body': row.body,
                'author_label': row.author_label or '', 'cover_url': row.cover_url or '',
            })
        rows = filter_scoped_rows(
            list(MannaTestimony.objects.filter(enabled=True).order_by('sort_order', 'id')),
            lang, plat, app_key=app,
        )
        return ApiResponse(data=[{
            'id': r.id, 'title': r.title, 'author_label': r.author_label or '',
            'cover_url': r.cover_url or '', 'body': r.body,
        } for r in rows])
