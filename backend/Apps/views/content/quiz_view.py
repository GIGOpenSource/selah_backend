#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah 3.0 Quiz App API：list / start / answer / result。"""
import logging

from django.utils import timezone as dj_tz
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import Quiz, QuizQuestion, QuizAttempt
from Apps.views.content.serializers import QuizStartSerializer, QuizAnswerSerializer
from tools.base_views import BaseViewSet
from tools.permissions import IsTokenValid
from tools.selah_quota import filter_scoped_rows, normalize_lang, normalize_platform, normalize_app_key, get_config
from tools.utils import ApiResponse

logger = logging.getLogger(__name__)


def _user_scope(user):
    lang = normalize_lang(getattr(user, 'system_lang', None) or 'en')
    plat = normalize_platform(getattr(user, 'platform', None) or '')
    app = normalize_app_key(getattr(user, 'app_key', None) or '')
    return lang, plat, app


def _question_public(q):
    return {
        'id': q.id,
        'stem': q.stem,
        'options': q.options or [],
        'sort_order': q.sort_order,
        # 故意不返回 correct_key / explain（完成结果接口再给）
    }


class QuizViewSet(BaseViewSet, ViewSet):
    """测验：需登录。"""
    permission_classes = [IsTokenValid]

    @extend_schema(summary=_('测验列表'))
    @action(detail=False, methods=['get'], url_path='list')
    def list_quizzes(self, request):
        if not get_config('quiz_enabled', True):
            return ApiResponse(data=[])
        lang, plat, app = _user_scope(request.user)
        rows = list(
            Quiz.objects.filter(is_deleted=False, enabled=True).order_by('sort_order', 'id')
        )
        picked = filter_scoped_rows(rows, lang, plat, group_key=None, app_key=app)
        data = []
        for r in picked:
            locked = bool(r.is_vip) and not request.user.is_vip_active
            qcount = QuizQuestion.objects.filter(quiz_id=r.id, enabled=True).count()
            data.append({
                'id': r.id,
                'title': r.title,
                'subtitle': r.subtitle,
                'cover_url': r.cover_url,
                'is_vip': r.is_vip,
                'locked': locked,
                'question_count': qcount,
            })
        return ApiResponse(data=data)

    @extend_schema(summary=_('开始测验'))
    @action(detail=False, methods=['post'], url_path='start')
    def start(self, request):
        ser = QuizStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        quiz = Quiz.objects.filter(
            id=ser.validated_data['quiz_id'], is_deleted=False, enabled=True,
        ).first()
        if not quiz:
            return ApiResponse(message='not found', code=404)
        if quiz.is_vip and not request.user.is_vip_active:
            return ApiResponse(message='vip required', code=403)
        questions = list(
            QuizQuestion.objects.filter(quiz_id=quiz.id, enabled=True).order_by('sort_order', 'id')
        )
        if not questions:
            return ApiResponse(message='no questions', code=400)
        attempt = QuizAttempt.objects.create(
            user_id=request.user.id,
            quiz_id=quiz.id,
            status=QuizAttempt.STATUS_IN_PROGRESS,
            answers={},
            score=0,
            total=len(questions),
        )
        return ApiResponse(data={
            'attempt_id': attempt.id,
            'quiz_id': quiz.id,
            'title': quiz.title,
            'total': len(questions),
            'questions': [_question_public(q) for q in questions],
        }, code=201)

    @extend_schema(summary=_('提交单题答案'))
    @action(detail=False, methods=['post'], url_path='answer')
    def answer(self, request):
        ser = QuizAnswerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        attempt = QuizAttempt.objects.filter(
            id=ser.validated_data['attempt_id'],
            user_id=request.user.id,
            status=QuizAttempt.STATUS_IN_PROGRESS,
        ).first()
        if not attempt:
            return ApiResponse(message='attempt not found', code=404)
        qid = ser.validated_data['question_id']
        q = QuizQuestion.objects.filter(id=qid, quiz_id=attempt.quiz_id, enabled=True).first()
        if not q:
            return ApiResponse(message='question not found', code=404)
        answers = dict(attempt.answers or {})
        picked = (ser.validated_data['option_key'] or '').strip()
        answers[str(qid)] = picked
        attempt.answers = answers
        attempt.save(update_fields=['answers'])
        correct_key = (q.correct_key or '').strip()
        is_correct = picked.lower() == correct_key.lower()
        return ApiResponse(data={
            'attempt_id': attempt.id,
            'answered': len(answers),
            'total': attempt.total,
            'question_id': q.id,
            'correct_key': q.correct_key,
            'picked_key': picked,
            'correct': is_correct,
            # 可选：要解析再加下一行
            # 'explain': q.explain or '',
        })

    @extend_schema(summary=_('测验结果'))
    @action(detail=False, methods=['get'], url_path='result')
    def result(self, request):
        try:
            attempt_id = int(request.query_params.get('attempt_id') or 0)
        except (TypeError, ValueError):
            attempt_id = 0
        attempt = QuizAttempt.objects.filter(
            id=attempt_id, user_id=request.user.id,
        ).first()
        if not attempt:
            return ApiResponse(message='attempt not found', code=404)
        questions = list(
            QuizQuestion.objects.filter(quiz_id=attempt.quiz_id, enabled=True).order_by('sort_order', 'id')
        )
        answers = attempt.answers or {}
        score = 0
        detail = []
        for q in questions:
            picked = answers.get(str(q.id)) or answers.get(q.id)
            picked_s = ('' if picked is None else str(picked)).strip()
            correct_s = (q.correct_key or '').strip()
            ok = picked_s.lower() == correct_s.lower()
            if ok:
                score += 1
            detail.append({
                'question_id': q.id,
                'stem': q.stem,
                'options': q.options or [],
                'correct_key': q.correct_key,
                'picked_key': picked,
                'correct': ok,
                'explain': q.explain or '',
            })
        if attempt.status != QuizAttempt.STATUS_COMPLETED:
            attempt.score = score
            attempt.total = len(questions)
            attempt.status = QuizAttempt.STATUS_COMPLETED
            attempt.completed_at = dj_tz.now()
            attempt.save(update_fields=['score', 'total', 'status', 'completed_at'])
        quiz = Quiz.objects.filter(id=attempt.quiz_id).first()
        return ApiResponse(data={
            'attempt_id': attempt.id,
            'quiz_id': attempt.quiz_id,
            'title': quiz.title if quiz else '',
            'score': attempt.score,
            'total': attempt.total,
            'detail': detail,
        })
