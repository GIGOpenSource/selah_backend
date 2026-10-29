#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
冷启动推荐：兴趣池 / 运营池按配置比例混排，信仰标签软优先。

主入口 build_feed(user, page_size)：
1. 基础池：上架语录 + 国家过滤 + 排除不喜欢；Free 用户过滤 VIP 语录
2. 有画像时按 cold_start_interest_ratio / ops_ratio（默认 70/30）取兴趣匹配与冷启动运营池
3. 曝光去重窗口 expose_dedupe_window；不足时回退全量池并打散

比例约定：
- interest_ratio : ops_ratio 默认 70:30（配置可改）
- 无画像（无兴趣标签，或未完成引导且未完成新人喜欢）时 n_interest=0，整页走运营/回退池
"""
import logging
import random

from django.db.models import Q

from models.models import Quote, UserContentHistory, UserDislike, UserLike
from tools.selah_quota import get_configs

logger = logging.getLogger(__name__)


def _pool_limit(page_size):
    """候选池上限：约 page_size×10，最少 80，避免远程库扫过大。"""
    return max(int(page_size) * 10, 80)


def _recent_ids(user_id, window):
    """
    最近 window 条曝光内容 ID，用于去重。

    数据源：UserContentHistory，按 id 倒序取前 window。
    窗口内 ID 不会进入兴趣/运营初选；回退仍可能再排除 recent。
    """
    qs = UserContentHistory.objects.filter(user_id=user_id).order_by('-id')[:window]
    return {r.content_id for r in qs}


def _country_q(user):
    """
    国家过滤 Query：空 countries = 全球可见。

    - 用户无 register_country → 不加过滤（看全部国家策略的语录）
    - 有国家码：语录 countries=[]（全球）或 contains 该国（兼容大小写/首字母大写）

    边界：countries 存脏格式时可能漏匹配；运营应统一大写 ISO。
    """
    country = (getattr(user, 'register_country', None) or '').strip().upper()
    if not country:
        return Q()
    return (
        Q(countries=[])
        | Q(countries__contains=[country])
        | Q(countries__contains=[country.lower()])
        | Q(countries__contains=[country.capitalize()])
    )



def content_scope_q(user, *, match_language=False, request=None):
    """
    内容库三维可见性：空维=全部；有用户维则匹配该维 ∪ 全局行。

    - platform / app_key：按用户画像过滤；用户无值时回退请求头/Query
      （X-App-Key / X-Platform），避免 H5/旧号空维看穿全库
    - language：仅 match_language=True 时启用（Topic/Wallpaper）；
      Quote.language 是正文语言，由推荐/运营另行处理，默认不在此过滤
    """
    from tools.selah_quota import (
        normalize_platform, normalize_app_key, normalize_lang, _request_query_get,
    )

    plat = normalize_platform(getattr(user, 'platform', '') or '')
    app = normalize_app_key(getattr(user, 'app_key', '') or '')
    lang = normalize_lang(getattr(user, 'system_lang', '') or '')

    if request is not None:
        headers = getattr(request, 'headers', None) or {}
        if not app:
            app = normalize_app_key(
                headers.get('X-App-Key')
                or headers.get('app-key')
                or _request_query_get(request, 'app_key')
                or ''
            )
        if not plat:
            plat = normalize_platform(
                _request_query_get(request, 'platform')
                or headers.get('X-Platform')
                or headers.get('x-platform')
                or headers.get('platform')
                or ''
            )
        if match_language and not lang:
            lang = normalize_lang(
                _request_query_get(request, 'language')
                or (headers.get('Accept-Language') or '').split(',')[0]
                or ''
            )

    q = Q()
    if plat:
        q &= Q(platform='') | Q(platform=plat)
    if app:
        q &= Q(app_key='') | Q(app_key=app)
    if match_language and lang:
        q &= Q(language='') | Q(language=lang)
    return q


def content_platform_q(user_or_platform):
    """兼容旧名：仅设备维；新代码请用 content_scope_q。"""
    from tools.selah_quota import normalize_platform
    if hasattr(user_or_platform, 'platform'):
        return content_scope_q(user_or_platform, match_language=False)
    plat = normalize_platform(user_or_platform or '')
    if not plat:
        return Q()
    return Q(platform='') | Q(platform=plat)


def _base_qs(user, request=None):
    """
    可推荐基础 QuerySet。

    条件叠加：
    1. is_deleted=False, status=1（上架）
    2. _country_q 国家可见性
    3. content_scope_q：app_key×platform（语录正文 language 不在此硬过滤）
    4. 排除 UserDislike 中的 content_id
    5. 非 VIP（含试用失效后）：is_vip=False，免费用户看不到 VIP 语录
       VIP/试用 is_vip_active=True 可看全量（含 is_vip 语录）
    """
    qs = Quote.objects.filter(is_deleted=False, status=1).filter(_country_q(user)).filter(
        content_scope_q(user, match_language=False, request=request)
    )
    disliked = list(UserDislike.objects.filter(user_id=user.id).values_list('content_id', flat=True))
    if disliked:
        qs = qs.exclude(id__in=disliked)
    # Free：过滤 VIP 语录；VIP/试用可看全量
    if not getattr(user, 'is_vip_active', False):
        qs = qs.filter(is_vip=False)
    return qs


def _tag_match(qs, tags):
    """兴趣标签 OR 匹配；tags 为空则返回空集（避免无标签时误返回全库）。"""
    if not tags:
        return qs.none()
    q = Q()
    for t in tags:
        q |= Q(interest_tags__contains=[t])
    return qs.filter(q)


def _interest_tags_for_user(user, like_soft_signal=True):
    """
    组装兴趣标签列表：Onboarding interest_tags +（可选）喜欢软信号。

    like_soft_signal 开启且 newcomer_like_completed：
      取最近最多 80 条 UserLike → 对应 Quote.interest_tags 并入兴趣池（去重保序）。
    作用：新人喜欢引导完成后，把「喜欢过的主题」当作弱兴趣，缓解冷启动。
    """
    interest = list(user.interest_tags or [])
    seen = set(interest)
    if like_soft_signal and getattr(user, 'newcomer_like_completed', False):
        liked_ids = list(
            UserLike.objects.filter(user_id=user.id).order_by('-id').values_list('content_id', flat=True)[:80]
        )
        if liked_ids:
            for tags in Quote.objects.filter(id__in=liked_ids).values_list('interest_tags', flat=True):
                for tag in (tags or []):
                    if tag and tag not in seen:
                        interest.append(tag)
                        seen.add(tag)
    return interest


def _faith_aliases(faith_type):
    """
    信仰类型别名集；文档枚举 learning，兼容历史 exploring。

    软排序时用别名集合与 Quote.faith_tags 做交集/子串匹配。
    """
    ft = (faith_type or '').strip().lower()
    if not ft:
        return set()
    aliases = {ft, faith_type}
    if ft in ('learning', 'exploring'):
        aliases.update({'learning', 'exploring'})
    return aliases


def _soft_faith_sort(ids, faith_type):
    """
    信仰软优先：匹配 faith_tags 的排前，组内随机；不做硬过滤。

    无信仰或空 ids：整表 shuffle 后返回。
    匹配失败的语录仍保留在 miss 段，保证池量。
    """
    aliases = _faith_aliases(faith_type)
    if not aliases or not ids:
        random.shuffle(ids)
        return ids
    quotes = Quote.objects.filter(id__in=ids).only('id', 'faith_tags')
    hit, miss = [], []
    for q in quotes:
        tags = [str(t).lower() for t in (q.faith_tags or [])]
        # 精确集合交或别名子串命中均算「信仰相关」
        if aliases & set(tags) or any(a in t for a in aliases for t in tags):
            hit.append(q.id)
        else:
            miss.append(q.id)
    random.shuffle(hit)
    random.shuffle(miss)
    return hit + miss


def build_feed(user, page_size=10, request=None):
    """
    组装一页首页 Feed（Quote 列表，顺序即展示顺序）。

    步骤：
    1. 读配置：曝光窗、70/30 比例、like_soft_signal
    2. 算兴趣标签；判定 has_profile（有兴趣且：完成引导 或 完成新人喜欢）
    3. 按比例算 n_interest / n_ops；无画像则 n_interest=0
    4. 兴趣池：标签匹配 ∩ 基础池 − recent → 信仰软排 → 取满 n_interest
    5. 运营池：in_cold_start_pool=True ∩ 基础池 − recent → 信仰软排 → 补到 page_size
    6. 仍不足：fallback = 基础池 − (已选∪recent)，再信仰软排补足
    7. 再不足：放宽忽略 recent，shuffle 硬补（可能重复曝光，优于空页）
    8. 兴趣段/运营段分别 shuffle，拼页；去掉相邻相同 content 文案后再补回

    约束：同页 ID 去重；先兴趣后运营再回退；Free 已在 _base_qs 滤 VIP 语录。
    request: 可选，供 content_scope_q 在用户空维时回退 Header。
    """
    cfg = get_configs([
        'expose_dedupe_window',
        'cold_start_interest_ratio',
        'cold_start_ops_ratio',
        'like_soft_signal',
    ])
    window = int(cfg.get('expose_dedupe_window', 100) or 100)
    interest_ratio = int(cfg.get('cold_start_interest_ratio', 70) or 70)
    ops_ratio = int(cfg.get('cold_start_ops_ratio', 30) or 30)
    like_soft = bool(cfg.get('like_soft_signal', True))

    recent = _recent_ids(user.id, window)
    interest = _interest_tags_for_user(user, like_soft_signal=like_soft)
    # 跳过引导不算「有画像」；但新人喜欢完成可单独解锁 soft 兴趣
    onboarded = bool(user.onboarding_completed) and not user.onboarding_skipped
    like_ready = bool(getattr(user, 'newcomer_like_completed', False))
    has_profile = bool(interest) and (onboarded or like_ready)

    # 70/30：按配置归一到 page_size；无画像整页给运营池配额
    total = max(interest_ratio + ops_ratio, 1)
    n_interest = round(page_size * interest_ratio / total) if has_profile else 0
    n_ops = page_size - n_interest
    pool_n = _pool_limit(page_size)

    base = _base_qs(user, request=request)
    interest_pool = []
    if has_profile:
        interest_pool = list(
            _tag_match(base, interest).exclude(id__in=recent).values_list('id', flat=True)[:pool_n]
        )
        interest_pool = _soft_faith_sort(interest_pool, user.faith_type)

    # 运营冷启动池：运营标记 in_cold_start_pool 的语录
    ops_pool = list(
        base.filter(in_cold_start_pool=True).exclude(id__in=recent).values_list('id', flat=True)[:pool_n]
    )
    ops_pool = _soft_faith_sort(ops_pool, user.faith_type)

    # —— 按配额拾取：先填兴趣槽，再填运营槽；已选跳过 ——
    picked = []
    for i in interest_pool:
        if i not in picked:
            picked.append(i)
        if len(picked) >= n_interest:
            break

    for i in ops_pool:
        if i in picked:
            continue
        picked.append(i)
        if len(picked) >= n_interest + n_ops:
            break

    # —— 回退 1：全基础池，仍避开 recent 与已选 ——
    if len(picked) < page_size:
        fallback = list(base.exclude(id__in=set(picked) | recent).values_list('id', flat=True)[:pool_n])
        fallback = _soft_faith_sort(fallback, user.faith_type)
        for i in fallback:
            if i not in picked:
                picked.append(i)
            if len(picked) >= page_size:
                break

    # —— 回退 2：库存极少时允许重复曝光窗口内内容，保证凑满一页 ——
    if len(picked) < page_size:
        more = list(base.exclude(id__in=picked).values_list('id', flat=True)[: max(pool_n // 2, 40)])
        random.shuffle(more)
        picked.extend(more[: page_size - len(picked)])

    # 兴趣段 / 运营段各自打散，保持段比例语义但减少固定顺序感
    head = picked[: max(n_interest, 0)]
    tail = picked[max(n_interest, 0): page_size]
    random.shuffle(head)
    random.shuffle(tail)
    ids = (head + tail)[:page_size]
    quotes = {q.id: q for q in Quote.objects.filter(id__in=ids)}
    ordered = [quotes[i] for i in ids if i in quotes]
    # 避免连续两条相同 content（极端重复文案）
    result = []
    last_text = None
    for q in ordered:
        if q.content == last_text:
            continue
        result.append(q)
        last_text = q.content
    # 因去重变短时，把被跳过的补到末尾（同页仍可能再出现相同文案，但不相邻）
    if len(result) < len(ordered):
        for q in ordered:
            if q not in result:
                result.append(q)
    return result[:page_size]


def ensure_default_strategy():
    """保证存在一条全局默认命中策略（priority=0，不可关）。"""
    from models.models import RecommendStrategy

    exists = RecommendStrategy.objects.filter(
        is_default=True, app_key='', language='', platform='',
    ).exists()
    if not exists:
        RecommendStrategy.objects.create(
            name='默认保底',
            enabled=True,
            priority=0,
            is_default=True,
            conditions=[],
            action={'match_dims': ['interest', 'faith']},
            app_key='',
            language='',
            platform='',
            remark='无匹配策略时走兴趣+信仰保底',
        )
