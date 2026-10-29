#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Manna 测验关卡 / 故事解锁（与 Selah content/quiz 隔离）。"""
import logging
import random

from django.db import transaction
from django.db.models import F
from django.utils import timezone as dj_tz
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import (
    MannaLedger, MannaQuizAttempt, MannaQuizLevel, MannaQuizQuestion, MannaQuizStory,
    MannaQuizStoryUnlock, MannaWallet,
)
from tools.manna_biz import (
    REASON_QUIZ_IN_BIBLE, REASON_QUIZ_REVIEW,
    annotate_story_progress, apply_manna_delta, ensure_manna_wallet,
    evaluate_manna_badges, story_blocks_for_client, touch_activity_day, user_local_now,
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
from tools.utils import ApiResponse
from Apps.views.analytics.view import record_content_open

logger = logging.getLogger(__name__)


def _attempt_questions(attempt):
    """本局题目：优先 detail.question_ids（切片关/快问），否则整关。"""
    all_qs = list(
        MannaQuizQuestion.objects.filter(level_id=attempt.level_id, enabled=True)
        .order_by('sort_order', 'id')
    )
    qids = (attempt.detail or {}).get('question_ids') or []
    if not qids:
        return all_qs
    by_id = {q.id: q for q in all_qs}
    wanted = []
    seen = set()
    for raw in qids:
        try:
            qid = int(raw)
        except (TypeError, ValueError):
            continue
        if qid in seen:
            continue
        seen.add(qid)
        q = by_id.get(qid)
        if q:
            wanted.append(q)
    return wanted or all_qs


def _cfg_bool(key, default=True):
    v = get_config(key, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v or '').strip().lower()
    if s in ('0', 'false', 'no', 'off', ''):
        return False
    if s in ('1', 'true', 'yes', 'on'):
        return True
    return bool(default)


def _scope(user):
    app = normalize_app_key(getattr(user, 'app_key', '') or '') or MANNA_APP_KEY
    lang = normalize_lang(getattr(user, 'system_lang', None) or 'en')
    plat = normalize_platform(getattr(user, 'platform', None) or '')
    return app, lang, plat


def _q_public(q):
    return {
        'id': q.id,
        'qtype': q.qtype,
        'stem': q.stem,
        'options': q.options or [],
        'sort_order': q.sort_order,
        'bible_ref': q.bible_ref or {},
    }


def _cfg_int(key, default=0):
    try:
        return max(0, int(get_config(key, default) or 0))
    except (TypeError, ValueError):
        return max(0, int(default or 0))


def _ordered_stories(app, lang, plat):
    return filter_scoped_rows(
        list(MannaQuizStory.objects.filter(enabled=True, app_key=app).order_by('sort_order', 'id')),
        lang, plat, app_key=app,
    )


def _spend_once(user, app, cost, reason, ref_type, ref_id):
    """按 user+reason+ref_id 幂等扣币；余额不足返回 None。cost<=0 不记账。"""
    cost = max(0, int(cost or 0))
    ref_id = str(ref_id or '')[:64]
    if cost <= 0:
        snap = get_manna_wallet_snapshot(user, app_key=app)
        return {'charged': False, 'already': False, 'balance': snap['balance'], 'cost': 0}
    ensure_manna_wallet(user, app)
    with transaction.atomic():
        wallet = MannaWallet.objects.select_for_update().get(
            user_id=user.id, app_key=app,
        )
        if MannaLedger.objects.filter(
            user_id=user.id, app_key=app, reason=reason, ref_id=ref_id,
        ).exists():
            return {
                'charged': False, 'already': True,
                'balance': int(wallet.balance or 0), 'cost': cost,
            }
        if int(wallet.balance or 0) < cost:
            return None
        apply_manna_delta(
            user, app, -cost, reason=reason,
            ref_type=str(ref_type or '')[:32], ref_id=ref_id, floor_zero=True,
        )
    bal = get_manna_wallet_snapshot(user, app_key=app)['balance']
    return {'charged': True, 'already': False, 'balance': bal, 'cost': cost}


def _ordered_levels(app, lang, plat):
    """作用域过滤后再按 level_no 排序（解锁链唯一序）。"""
    levels = filter_scoped_rows(
        list(MannaQuizLevel.objects.filter(enabled=True, app_key=app).order_by('sort_order', 'level_no', 'id')),
        lang, plat, app_key=app,
    )
    levels.sort(key=lambda lv: (
        int(getattr(lv, 'level_no', 0) or 0),
        int(getattr(lv, 'sort_order', 0) or 0),
        lv.id,
    ))
    return levels


def _level_is_locked(levels, lv, passed_ids):
    """上一关未通关且本关未通关 → 不可开局（真正未解锁）。"""
    try:
        idx = next(i for i, x in enumerate(levels) if x.id == lv.id)
    except StopIteration:
        return True
    if idx <= 0:
        return False
    prev = levels[idx - 1]
    return prev.id not in passed_ids and lv.id not in passed_ids


def _passed_level_ids(user_id, app):
    """通关口径与 finish 一致：完成且答对全部（score >= total）。"""
    return set(
        MannaQuizAttempt.objects.filter(
            user_id=user_id,
            app_key=app,
            status=MannaQuizAttempt.STATUS_COMPLETED,
            total__gt=0,
            score__gte=F('total'),
        ).values_list('level_id', flat=True)
    )


def _pick_current_level(levels, passed_ids):
    """当前关：第一个未锁定且未通关；全通则最后一关。"""
    if not levels:
        return None
    for lv in levels:
        if lv.id in passed_ids:
            continue
        if not _level_is_locked(levels, lv, passed_ids):
            return lv
    return levels[-1]


def _list_locked(levels, lv, passed_ids, current):
    """列表 locked：未解锁，或已通关且不是当前关。

    前端用 first(!locked) 当作「开打哪一关」，已通关关必须标 locked，
    否则会一直卡在更早的关（如第 2 关）而实际进度已到第 3 关。
    """
    if _level_is_locked(levels, lv, passed_ids):
        return True
    if lv.id in passed_ids and current is not None and lv.id != current.id:
        return True
    return False


@extend_schema(tags=[_('Manna Quiz')])
class MannaQuizViewSet(ViewSet):
    permission_classes = [IsTokenValid]

    @extend_schema(summary=_('测验首页'))
    @action(detail=False, methods=['get'], url_path='home')
    def home(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        ensure_manna_wallet(request.user, app)
        snap = get_manna_wallet_snapshot(request.user, app_key=app)
        levels = _ordered_levels(app, lang, plat)
        passed = _passed_level_ids(request.user.id, app)
        current = _pick_current_level(levels, passed)
        return ApiResponse(data={
            'balance': snap['balance'],
            'current_level': {
                'id': current.id, 'level_no': current.level_no, 'title': current.title,
                'time_limit_sec': current.time_limit_sec,
            } if current else None,
            'level_count': len(levels),
        })

    @extend_schema(summary=_('关卡列表'))
    @action(detail=False, methods=['get'], url_path='levels')
    def levels(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        levels = _ordered_levels(app, lang, plat)
        passed = _passed_level_ids(request.user.id, app)
        current = _pick_current_level(levels, passed)
        data = []
        for lv in levels:
            qcount = MannaQuizQuestion.objects.filter(level_id=lv.id, enabled=True).count()
            locked = _list_locked(levels, lv, passed, current)
            data.append({
                'id': lv.id, 'level_no': lv.level_no, 'title': lv.title,
                'time_limit_sec': lv.time_limit_sec, 'pass_score': lv.pass_score,
                'reward_manna': lv.reward_manna, 'fail_penalty': lv.fail_penalty,
                'question_count': qcount, 'passed': lv.id in passed, 'locked': locked,
            })
        return ApiResponse(data=data)

    @extend_schema(summary=_('开始测验'))
    @action(detail=False, methods=['post'], url_path='start')
    def start(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        try:
            level_id = int(request.data.get('level_id') or 0)
        except (TypeError, ValueError):
            return ApiResponse(message='level_id required', code=400)
        lv = MannaQuizLevel.objects.filter(id=level_id, enabled=True).first()
        if not lv:
            return ApiResponse(message='not found', code=404)
        levels = _ordered_levels(app, lang, plat)
        if not any(x.id == lv.id for x in levels):
            return ApiResponse(message='level out of scope', code=404)
        passed = _passed_level_ids(request.user.id, app)
        current = _pick_current_level(levels, passed)
        # 客户端常误传已通关的上一关（首页 first(!locked)、故事后 last_level_id）。
        # 无 force 时自动切到当前可玩关，避免「进度已到第 3 关却开第 2 关」。
        force = str(request.data.get('force') or '').strip().lower() in ('1', 'true', 'yes')
        if (
            not force
            and current is not None
            and lv.id in passed
            and lv.id != current.id
        ):
            lv = current
        if _level_is_locked(levels, lv, passed):
            return ApiResponse(message=_('关卡未解锁'), code=400)
        questions = list(
            MannaQuizQuestion.objects.filter(level_id=lv.id, enabled=True)
            .order_by('sort_order', 'id')
        )
        mode = str(request.data.get('mode') or '').strip()
        time_limit = int(lv.time_limit_sec or 0)
        if mode == 'rapid':
            n_take, time_limit = 6, 60
        elif mode == 'story_tf':
            n_take, time_limit = 3, 30
        else:
            n_take = 3
        if len(questions) > n_take:
            questions = random.sample(questions, n_take)
        else:
            questions = questions[:n_take]
        if not questions:
            return ApiResponse(message='no questions', code=400)
        qids = [q.id for q in questions]
        attempt = MannaQuizAttempt.objects.create(
            user_id=request.user.id, app_key=app, level_id=lv.id,
            status=MannaQuizAttempt.STATUS_IN_PROGRESS,
            total=len(questions),
            detail={'answers': {}, 'mode': mode, 'question_ids': qids},
        )
        record_content_open(
            request.user, 'quiz_level', lv.id,
            title=lv.title or '', app_key=app, level_no=lv.level_no, mode=mode or '',
        )
        snap = get_manna_wallet_snapshot(request.user, app_key=app)
        pass_delta = 10 * len(questions)
        return ApiResponse(data={
            'attempt_id': attempt.id,
            'level_id': lv.id,
            'level_no': lv.level_no,
            'title': lv.title,
            'time_limit_sec': time_limit,
            'mode': mode,
            'total': len(questions),
            'balance': snap['balance'],
            'preview_pass_delta': pass_delta,
            'preview_fail_delta': 0,
            'questions': [_q_public(q) for q in questions],
        }, code=201)


    @extend_schema(summary=_('提交单题'))
    @action(detail=False, methods=['post'], url_path='answer')
    def answer(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user)
        try:
            attempt_id = int(request.data.get('attempt_id') or 0)
            question_id = int(request.data.get('question_id') or 0)
        except (TypeError, ValueError):
            return ApiResponse(message='attempt_id/question_id required', code=400)
        option_key = str(request.data.get('option_key') or request.data.get('answer_key') or '')
        attempt = MannaQuizAttempt.objects.filter(
            id=attempt_id, user_id=request.user.id, app_key=app,
            status=MannaQuizAttempt.STATUS_IN_PROGRESS,
        ).first()
        if not attempt:
            return ApiResponse(message='attempt not found', code=404)
        q = MannaQuizQuestion.objects.filter(
            id=question_id, level_id=attempt.level_id, enabled=True,
        ).first()
        if not q:
            return ApiResponse(message='question not found', code=404)
        detail = dict(attempt.detail or {})
        answers = dict(detail.get('answers') or {})
        correct = option_key == q.answer_key
        answers[str(question_id)] = {
            'picked': option_key, 'correct': correct, 'answer_key': q.answer_key,
        }
        detail['answers'] = answers
        attempt.detail = detail
        attempt.save(update_fields=['detail'])
        return ApiResponse(data={
            'attempt_id': attempt.id,
            'question_id': question_id,
            'correct': correct,
            'answer_key': q.answer_key,
            'explanation': q.explanation or '',
            'bible_ref': q.bible_ref or {},
            'answered': len(answers),
            'total': attempt.total,
        })

    @extend_schema(summary=_('结束结算'))
    @action(detail=False, methods=['post'], url_path='finish')
    def finish(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        try:
            attempt_id = int(request.data.get('attempt_id') or 0)
        except (TypeError, ValueError):
            return ApiResponse(message='attempt_id required', code=400)
        with transaction.atomic():
            attempt = MannaQuizAttempt.objects.select_for_update().filter(
                id=attempt_id, user_id=request.user.id, app_key=app,
            ).first()
            if not attempt:
                return ApiResponse(message='attempt not found', code=404)
            if attempt.status == MannaQuizAttempt.STATUS_COMPLETED:
                return ApiResponse(data={
                    'attempt_id': attempt.id, 'score': attempt.score, 'total': attempt.total,
                    'manna_delta': attempt.manna_delta, 'passed': attempt.manna_delta >= 0,
                    'newly_earned': [],
                })
            lv = MannaQuizLevel.objects.filter(id=attempt.level_id).first()
            questions = _attempt_questions(attempt)
            answers = (attempt.detail or {}).get('answers') or {}
            mode = str((attempt.detail or {}).get('mode') or '')
            score = sum(1 for q in questions if (answers.get(str(q.id)) or {}).get('correct'))
            total = len(questions)
            pass_score = int(lv.pass_score if lv else 1)
            require_all = _cfg_bool('manna_quiz_require_all_correct', True)
            if require_all:
                passed = bool(total) and score == total
            else:
                passed = score >= pass_score
            # 方案 B：按对题数计分（每题 10），不再失败统一 -fail_penalty
            delta = 10 * int(score)
            apply_manna_delta(
                request.user, app, delta,
                reason='quiz_pass' if delta > 0 else 'quiz_fail',
                ref_type='manna_quiz_attempt', ref_id=str(attempt.id),
                floor_zero=True,
            )
            attempt.score = score
            attempt.total = total
            attempt.manna_delta = delta
            attempt.status = MannaQuizAttempt.STATUS_COMPLETED
            attempt.finished_at = dj_tz.now()
            attempt.save()
        local_dt, _tz = user_local_now(request.user, request.data.get('tz'))
        touch_activity_day(
            request.user, app, day=local_dt.date(),
            quiz_count=1, flags={'quiz': True},
        )
        newly = evaluate_manna_badges(request.user, app, language=lang, platform=plat)
        snap = get_manna_wallet_snapshot(request.user, app_key=app)
        next_level = None
        unlock_story = None
        levels = _ordered_levels(app, lang, plat)
        if passed and lv:
            nxt = next((x for x in levels if x.level_no == int(lv.level_no or 0) + 1), None)
            if nxt:
                next_level = {'id': nxt.id, 'level_no': nxt.level_no, 'title': nxt.title}
            if int(lv.level_no or 0) >= 2:
                rows = _ordered_stories(app, lang, plat)
                unlocked = set(
                    MannaQuizStoryUnlock.objects.filter(
                        user_id=request.user.id, app_key=app,
                    ).values_list('story_id', flat=True)
                )
                progress = annotate_story_progress(rows, unlocked)
                story = next(
                    (s for s in rows if progress.get(s.id, {}).get('available') and not progress.get(s.id, {}).get('unlocked')),
                    None,
                )
                if story:
                    unlock_story = {
                        'id': story.id, 'title': story.title,
                        'cover_url': story.cover_url or '',
                        'card_url': story.card_url or '',
                        'unlock_cost': 0,
                    }
        return ApiResponse(data={
            'attempt_id': attempt.id,
            'score': attempt.score,
            'total': attempt.total,
            'passed': passed,
            'manna_delta': attempt.manna_delta,
            'balance': snap['balance'],
            'level_no': lv.level_no if lv else 0,
            'next_level': next_level,
            'unlock_story': unlock_story,
            'newly_earned': newly,
        })

    @extend_schema(summary=_('测验结果'))
    @action(detail=False, methods=['get'], url_path='result')
    def result(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user)
        try:
            attempt_id = int(request.query_params.get('attempt_id') or 0)
        except (TypeError, ValueError):
            attempt_id = 0
        attempt = MannaQuizAttempt.objects.filter(
            id=attempt_id, user_id=request.user.id, app_key=app,
        ).first()
        if not attempt:
            return ApiResponse(message='attempt not found', code=404)
        lv = MannaQuizLevel.objects.filter(id=attempt.level_id).first()
        questions = _attempt_questions(attempt)
        answers = (attempt.detail or {}).get('answers') or {}
        detail = []
        for q in questions:
            ans = answers.get(str(q.id)) or {}
            detail.append({
                'question_id': q.id, 'stem': q.stem, 'options': q.options or [],
                'answer_key': q.answer_key, 'picked': ans.get('picked'),
                'correct': bool(ans.get('correct')), 'explanation': q.explanation or '',
                'bible_ref': q.bible_ref or {},
            })
        return ApiResponse(data={
            'attempt_id': attempt.id,
            'level_id': attempt.level_id,
            'title': lv.title if lv else '',
            'score': attempt.score,
            'total': attempt.total,
            'manna_delta': attempt.manna_delta,
            'status': attempt.status,
            'detail': detail,
        })

    @extend_schema(summary=_('测验消耗（In Bible / Review）'))
    @action(detail=False, methods=['post'], url_path='spend')
    def spend(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user)
        kind = str(request.data.get('kind') or '').strip().lower()
        try:
            attempt_id = int(request.data.get('attempt_id') or 0)
        except (TypeError, ValueError):
            attempt_id = 0
        try:
            question_id = int(request.data.get('question_id') or 0)
        except (TypeError, ValueError):
            question_id = 0
        if kind not in ('in_bible', 'review'):
            return ApiResponse(message='kind required', code=400)
        attempt = MannaQuizAttempt.objects.filter(
            id=attempt_id, user_id=request.user.id, app_key=app,
        ).first()
        if not attempt:
            return ApiResponse(message='attempt not found', code=404)
        if kind == 'in_bible':
            qids = {q.id for q in _attempt_questions(attempt)}
            if question_id and question_id not in qids:
                return ApiResponse(message='question not in attempt', code=400)
            cost = _cfg_int('manna_in_bible_cost', 10)
            reason = REASON_QUIZ_IN_BIBLE
            ref_id = f'{attempt.id}:{question_id or 0}'
            ref_type = 'manna_quiz_in_bible'
        else:
            cost = _cfg_int('manna_quiz_review_cost', 10)
            reason = REASON_QUIZ_REVIEW
            ref_id = str(attempt.id)
            ref_type = 'manna_quiz_review'
        paid = _spend_once(request.user, app, cost, reason, ref_type, ref_id)
        if paid is None:
            return ApiResponse(message='insufficient manna', code=400)
        return ApiResponse(data=paid)

    @extend_schema(summary=_('故事列表'))
    @action(detail=False, methods=['get'], url_path='stories')
    def stories(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        rows = _ordered_stories(app, lang, plat)
        unlocked = set(
            MannaQuizStoryUnlock.objects.filter(
                user_id=request.user.id, app_key=app,
            ).values_list('story_id', flat=True)
        )
        progress = annotate_story_progress(rows, unlocked)
        return ApiResponse(data=[{
            'id': s.id, 'title': s.title, 'cover_url': s.cover_url or '',
            'card_url': s.card_url or '',
            'unlock_cost': 0, 'unlocked': progress.get(s.id, {}).get('unlocked', False),
            'available': progress.get(s.id, {}).get('available', False),
            'locked': progress.get(s.id, {}).get('locked', True),
            'linked_level_id': s.linked_level_id,
        } for s in rows])

    @extend_schema(summary=_('学完故事（不扣币）'))
    @action(detail=False, methods=['post'], url_path='story-unlock')
    def story_unlock(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        try:
            story_id = int(request.data.get('story_id') or request.data.get('id') or 0)
        except (TypeError, ValueError):
            return ApiResponse(message='story_id required', code=400)
        rows = _ordered_stories(app, lang, plat)
        story = next((s for s in rows if s.id == story_id), None)
        if not story:
            return ApiResponse(message='not found', code=404)
        if MannaQuizStoryUnlock.objects.filter(
            user_id=request.user.id, app_key=app, story_id=story.id,
        ).exists():
            return ApiResponse(data={'unlocked': True, 'already': True, 'newly_earned': []})
        unlocked = set(
            MannaQuizStoryUnlock.objects.filter(
                user_id=request.user.id, app_key=app,
            ).values_list('story_id', flat=True)
        )
        access = annotate_story_progress(rows, unlocked).get(story.id) or {}
        if access.get('locked'):
            return ApiResponse(message='complete previous story', code=400)
        with transaction.atomic():
            if MannaQuizStoryUnlock.objects.select_for_update().filter(
                user_id=request.user.id, app_key=app, story_id=story.id,
            ).exists():
                return ApiResponse(data={'unlocked': True, 'already': True, 'newly_earned': []})
            MannaQuizStoryUnlock.objects.create(
                user_id=request.user.id, app_key=app, story_id=story.id,
            )
        newly = evaluate_manna_badges(request.user, app, language=lang, platform=plat)
        bal = get_manna_wallet_snapshot(request.user, app_key=app)['balance']
        return ApiResponse(data={'unlocked': True, 'balance': bal, 'newly_earned': newly}, code=201)

    @extend_schema(summary=_('故事详情'))
    @action(detail=False, methods=['get'], url_path='story-detail')
    def story_detail(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        try:
            sid = int(request.query_params.get('id') or 0)
        except (TypeError, ValueError):
            sid = 0
        rows = _ordered_stories(app, lang, plat)
        story = next((s for s in rows if s.id == sid), None)
        if not story:
            return ApiResponse(message='not found', code=404)
        unlocked_ids = set(
            MannaQuizStoryUnlock.objects.filter(
                user_id=request.user.id, app_key=app,
            ).values_list('story_id', flat=True)
        )
        access = annotate_story_progress(rows, unlocked_ids).get(story.id) or {}
        if access.get('locked'):
            # 业务锁定用 400，避免 App HTTP 层把 403 当成会话过期踢登
            return ApiResponse(message='locked', code=400)
        record_content_open(
            request.user, 'quiz_story', story.id,
            title=story.title or '', app_key=app,
            linked_level_id=story.linked_level_id or 0,
        )
        return ApiResponse(data={
            'id': story.id, 'title': story.title, 'cover_url': story.cover_url or '',
            'card_url': story.card_url or story.cover_url or '',
            'cover_image_index': int(getattr(story, 'cover_image_index', 0) or 0),
            'blocks': story_blocks_for_client(story),
            'body': story.body or '', 'knowledge_cards': story.knowledge_cards or [],
            'unlock_cost': 0,
            'unlocked': bool(access.get('unlocked')),
            'available': bool(access.get('available')),
            'linked_level_id': story.linked_level_id,
        })
