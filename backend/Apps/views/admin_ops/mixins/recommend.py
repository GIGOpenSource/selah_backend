#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""AdminOps mixin — split from view.py; logic unchanged."""
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from django.db.models import Q

from Apps.views.admin_ops._helpers import (  # noqa: F401
    RecommendStrategy, PERM_CONTENT, has_perm, ApiResponse,
    normalize_app_key, normalize_lang, normalize_platform, _deny_app_write,
)

class RecommendMixin:
    def _serialize_strategy(self, s):
        return {
            'id': s.id,
            'name': s.name,
            'enabled': s.enabled,
            'priority': s.priority,
            'is_default': s.is_default,
            'conditions': s.conditions or [],
            'action': s.action or {},
            'app_key': s.app_key or '',
            'language': s.language or '',
            'platform': s.platform or '',
            'remark': s.remark or '',
            'created_at': s.created_at.isoformat() if s.created_at else None,
            'updated_at': s.updated_at.isoformat() if s.updated_at else None,
        }

    @extend_schema(summary=_('命中策略列表'))
    @action(detail=False, methods=['get'], url_path='recommend-strategies')
    def recommend_strategies(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        from tools.selah_recommend import ensure_default_strategy
        ensure_default_strategy()
        qs = RecommendStrategy.objects.all().order_by('-priority', 'id')
        app = normalize_app_key(request.query_params.get('app_key') or '')
        lang = normalize_lang(request.query_params.get('language') or '')
        plat = normalize_platform(request.query_params.get('platform') or '')
        if app:
            qs = qs.filter(Q(app_key='') | Q(app_key=app))
        if lang:
            qs = qs.filter(Q(language='') | Q(language=lang))
        if plat:
            qs = qs.filter(Q(platform='') | Q(platform=plat))
        rows = [self._serialize_strategy(s) for s in qs]
        enabled_custom = sum(1 for s in rows if s.get('enabled') and not s.get('is_default'))
        tip = ''
        if not rows:
            tip = '暂无命中策略。系统将自动创建默认保底策略；也可点击「填充测试策略」写入验收用例。'
        elif enabled_custom == 0:
            tip = '当前没有启用的非默认策略，命中池仅走默认保底匹配。建议新建带条件的策略，或点击「填充测试策略」后再验收。'
        return ApiResponse(data={
            'list': rows,
            'enabled_custom_count': enabled_custom,
            'tip': tip,
        })

    @extend_schema(summary=_('保存命中策略'))
    @action(detail=False, methods=['post'], url_path='recommend-strategy-save')
    def recommend_strategy_save(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        data = request.data or {}
        sid = data.get('id')
        app = normalize_app_key(data.get('app_key') or '')
        denied = _deny_app_write(request, app)
        if denied:
            return denied
        lang = normalize_lang(data.get('language') or '')
        plat = normalize_platform(data.get('platform') or '')
        name = (data.get('name') or '').strip()
        if not name:
            return ApiResponse(message='名称必填', code=400)
        is_default = bool(data.get('is_default'))
        enabled = bool(data.get('enabled', True))
        conditions = data.get('conditions') if isinstance(data.get('conditions'), list) else []
        action = data.get('action') if isinstance(data.get('action'), dict) else {'match_dims': ['interest', 'faith']}
        priority = int(data.get('priority') or 0)
        if not is_default and not conditions:
            return ApiResponse(message='非默认策略必须填写条件', code=400)
        if is_default and not enabled:
            return ApiResponse(message='默认策略不能关闭', code=400)

        row = None
        if sid:
            row = RecommendStrategy.objects.filter(id=sid).first()
            if not row:
                return ApiResponse(message='策略不存在', code=404)
            if row.is_default:
                is_default = True
                enabled = True
        else:
            row = RecommendStrategy()

        if is_default:
            RecommendStrategy.objects.filter(
                is_default=True, app_key=app, language=lang, platform=plat,
            ).exclude(id=row.id or 0).update(is_default=False)

        row.name = name
        row.enabled = enabled
        row.priority = priority
        row.is_default = is_default
        row.conditions = conditions
        row.action = action
        row.app_key = app
        row.language = lang
        row.platform = plat
        row.remark = (data.get('remark') or '')[:255]
        row.save()
        return ApiResponse(data=self._serialize_strategy(row), message='ok')

    @extend_schema(summary=_('删除命中策略'))
    @action(detail=False, methods=['post'], url_path='recommend-strategy-delete')
    def recommend_strategy_delete(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        sid = (request.data or {}).get('id')
        row = RecommendStrategy.objects.filter(id=sid).first()
        if not row:
            return ApiResponse(message='策略不存在', code=404)
        if row.is_default:
            return ApiResponse(message='默认策略不能删除', code=400)
        denied = _deny_app_write(request, row.app_key or '')
        if denied:
            return denied
        row.delete()
        return ApiResponse(message='deleted')

    @extend_schema(summary=_('填充命中测试策略'))
    @action(detail=False, methods=['post'], url_path='recommend-strategies-seed-test')
    def recommend_strategies_seed_test(self, request):
        """写入若干可验收的测试策略（幂等：同名同范围已存在则跳过）。"""
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        from tools.selah_recommend import ensure_default_strategy
        ensure_default_strategy()
        app = normalize_app_key((request.data or {}).get('app_key') or '')
        denied = _deny_app_write(request, app)
        if denied:
            return denied
        lang = normalize_lang((request.data or {}).get('language') or '')
        plat = normalize_platform((request.data or {}).get('platform') or '')
        seeds = [
            {
                'name': '测试-探索中用户',
                'priority': 100,
                'conditions': [{'field': 'faith_type', 'op': 'eq', 'value': 'learning'}],
                'action': {'match_dims': ['faith', 'interest']},
                'remark': '验收用：信仰=探索中',
            },
            {
                'name': '测试-祷告兴趣',
                'priority': 90,
                'conditions': [{'field': 'interest_tags', 'op': 'contains', 'value': 'Prayer'}],
                'action': {'match_dims': ['interest']},
                'remark': '验收用：兴趣含祷告',
            },
            {
                'name': '测试-每日祷告',
                'priority': 80,
                'conditions': [{'field': 'prayer_frequency', 'op': 'eq', 'value': 'daily'}],
                'action': {'match_dims': ['interest', 'faith']},
                'remark': '验收用：祷告频率=每天',
            },
            {
                'name': '测试-平安主题',
                'priority': 70,
                'conditions': [{'field': 'interest_tags', 'op': 'contains', 'value': 'Peace'}],
                'action': {'match_dims': ['interest']},
                'remark': '验收用：兴趣含平安（含旧 anxiety 映射）',
            },
        ]
        created = []
        skipped = []
        for s in seeds:
            exists = RecommendStrategy.objects.filter(
                name=s['name'], app_key=app, language=lang, platform=plat,
            ).exists()
            if exists:
                skipped.append(s['name'])
                continue
            row = RecommendStrategy.objects.create(
                name=s['name'],
                enabled=True,
                priority=s['priority'],
                is_default=False,
                conditions=s['conditions'],
                action=s['action'],
                app_key=app,
                language=lang,
                platform=plat,
                remark=s['remark'],
            )
            created.append(self._serialize_strategy(row))
        enabled_custom = RecommendStrategy.objects.filter(
            enabled=True, is_default=False,
        ).filter(Q(app_key='') | Q(app_key=app)).count()
        tip = ''
        if enabled_custom == 0 and not created:
            tip = '当前没有启用的非默认策略，命中池仅走默认保底；请新建或点击「填充测试策略」。'
        return ApiResponse(
            message='ok',
            data={
                'created': created,
                'skipped': skipped,
                'enabled_custom_count': enabled_custom,
                'tip': tip,
            },
        )

    @extend_schema(summary=_('立即重算同池得分'))
    @action(detail=False, methods=['post'], url_path='pool-rank-recompute')
    def pool_rank_recompute(self, request):
        if not has_perm(request.user, PERM_CONTENT):
            return ApiResponse(message=_('无权限'), code=403)
        from tools.pool_rank import recompute_all_scores
        n = recompute_all_scores()
        return ApiResponse(message='ok', data={'updated': n})
