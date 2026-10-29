#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""同池排序：按天互动 + 时间衰减 + 平滑分享/点赞率 → pool_rank_score。"""
import math
from datetime import date, timedelta

from django.db.models import F
from django.utils import timezone as dj_tz

from models.models import Quote, QuoteDailyEngage
from tools.selah_quota import get_configs


def _today():
    return dj_tz.localdate() if hasattr(dj_tz, 'localdate') else date.today()


def bump_engage(quote_id, *, expose=0, like=0, share=0, day=None):
    """累加当日互动计数。"""
    if not quote_id:
        return
    day = day or _today()
    row, _ = QuoteDailyEngage.objects.get_or_create(
        quote_id=int(quote_id),
        day=day,
        defaults={'expose_count': 0, 'like_count': 0, 'share_count': 0},
    )
    updates = {}
    if expose:
        updates['expose_count'] = F('expose_count') + int(expose)
    if like:
        updates['like_count'] = F('like_count') + int(like)
    if share:
        updates['share_count'] = F('share_count') + int(share)
    if updates:
        QuoteDailyEngage.objects.filter(pk=row.pk).update(**updates)


def _decay_weight(age_days, half_life):
    if half_life <= 0:
        return 0.0
    return math.pow(0.5, float(age_days) / float(half_life))


def score_quote_from_rows(rows, today, cfg):
    """根据日互动行算综合得分（不含信仰微调）。"""
    half = float(cfg.get('pool_rank_half_life_days') or 7)
    window = int(cfg.get('pool_rank_window_days') or 56)
    like_a = float(cfg.get('pool_rank_like_alpha') or 1)
    like_b = float(cfg.get('pool_rank_like_beta') or 50)
    share_a = float(cfg.get('pool_rank_share_alpha') or 0.5)
    share_b = float(cfg.get('pool_rank_share_beta') or 100)
    w_share = float(cfg.get('pool_rank_share_weight') or 65) / 100.0
    w_like = float(cfg.get('pool_rank_like_weight') or 35) / 100.0

    exp_w = like_w = share_w = 0.0
    for r in rows:
        age = (today - r.day).days
        if age < 0 or age > window:
            continue
        w = _decay_weight(age, half)
        exp_w += (r.expose_count or 0) * w
        like_w += (r.like_count or 0) * w
        share_w += (r.share_count or 0) * w

    like_rate = (like_w + like_a) / (exp_w + like_b) if (exp_w + like_b) > 0 else 0.0
    share_rate = (share_w + share_a) / (exp_w + share_b) if (exp_w + share_b) > 0 else 0.0
    return w_share * share_rate + w_like * like_rate


def load_pool_rank_cfg(user=None, request=None):
    keys = [
        'pool_rank_enabled',
        'pool_rank_share_weight',
        'pool_rank_like_weight',
        'pool_rank_half_life_days',
        'pool_rank_window_days',
        'pool_rank_like_alpha',
        'pool_rank_like_beta',
        'pool_rank_share_alpha',
        'pool_rank_share_beta',
        'pool_rank_faith_bonus',
        'pool_rank_tie_break',
    ]
    if user is not None:
        return get_configs(
            keys,
            app_key=getattr(user, 'app_key', '') or '',
            language=getattr(user, 'system_lang', '') or '',
            platform=getattr(user, 'platform', '') or '',
        )
    return get_configs(keys)


def recompute_all_scores(quote_ids=None, cfg=None):
    """重算语录 pool_rank_score 并写回 Quote。"""
    cfg = cfg or load_pool_rank_cfg()
    today = _today()
    window = int(cfg.get('pool_rank_window_days') or 56)
    start = today - timedelta(days=window)

    qs = QuoteDailyEngage.objects.filter(day__gte=start, day__lte=today)
    if quote_ids is not None:
        qs = qs.filter(quote_id__in=list(quote_ids))

    by_qid = {}
    for row in qs.iterator():
        by_qid.setdefault(row.quote_id, []).append(row)

    target_ids = list(quote_ids) if quote_ids is not None else list(
        Quote.objects.filter(is_deleted=False).values_list('id', flat=True)
    )
    updated = 0
    for qid in target_ids:
        score = score_quote_from_rows(by_qid.get(qid, []), today, cfg)
        Quote.objects.filter(id=qid).update(pool_rank_score=score)
        updated += 1
    return updated
