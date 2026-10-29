#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
运营看板统计口径与查询。

公开聚合入口：
- parse_filters / overview：区间 KPI + 日序列
- series_dau / series_new_users / series_revenue / series_orders_kpi：日趋势
- retention_table：注册 cohort D0–D30 留存
- geo_device_dist / language_dist：国家/设备/语言分布
- events_aggregate：§9.4 埋点 UV/PV（目录内无数据仍返回 0）
- manna_practice：Manna 功课留存 KPI
- manna_content_opens：Manna 各内容打开 PV/UV（content_open）
- closed_deal_qs：成交收据（非退款且金额>0）

筛选维度：from/to、system_lang、register_country、platform、app_key（仅 role=user）。

口径速查：
- DAU：User.last_active_at 落在当日的用户数（非埋点 UV）
- 留存：注册日 cohort，D+N 当日 last_active 或有 AnalyticsEvent 算活跃
- ARPPU：区间/日成交金额 ÷ 付费人数（分）；付费率 = 付费人数 ÷ DAU（或区间 unique_active）
- platform：筛 User.platform；成交在无用户筛时也可直接筛收据 platform（见 closed_deal_qs 分支）
"""
from datetime import date, datetime, timedelta, timezone as dt_tz
from collections import defaultdict

from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDate
from django.utils import timezone as dj_tz

from models.models import (
    User, IapReceipt, AnalyticsEvent, ActivityDay, MannaLedger, MannaQuizAttempt, MannaWallet,
)

# ---------------------------------------------------------------------------
# §9.4 全量埋点目录（含中文名）；无数据时仍返回 uv/pv=0
# kind: page=页面曝光, button=按钮/动作, other=其他
# 运营台事件表按此顺序铺底，再追加目录外事件
# ---------------------------------------------------------------------------
EVENT_CATALOG = (
    # 9.4.1 登录
    ('login_success', '登录成功', 'button'),
    ('bind_prompt_show', '绑定弱提示展示', 'page'),
    ('bind_prompt_click', '点击去绑定', 'button'),
    ('bind_prompt_dismiss', '关闭绑定弱提示', 'button'),
    ('bind_success', '三方绑定成功', 'button'),
    ('bind_conflict_show', '绑定冲突弹窗展示', 'page'),
    ('bind_conflict_confirm', '确认切换到三方账号', 'button'),
    ('bind_conflict_cancel', '取消绑定冲突', 'button'),
    # 9.4.2 Onboarding
    ('onboarding_page_view', '进入引导页', 'page'),
    ('onboarding_faith_select', '选择信仰类型', 'button'),
    ('onboarding_prayer_frequency_select', '选择祷告频率', 'button'),
    ('onboarding_interest_select', '选择兴趣主题', 'button'),
    ('onboarding_basic_info', '提交基础信息', 'button'),
    ('onboarding_skip', '跳过引导', 'button'),
    ('onboarding_complete', '完成引导', 'button'),
    # 9.4.3 首页语录
    ('home_quote_view', '进入首页', 'page'),
    ('quote_expose', '语录曝光', 'page'),
    ('quote_dwell', '语录停留/滑离', 'other'),
    ('quote_like', '喜欢/取消喜欢', 'button'),
    ('quote_dislike', '不喜欢', 'button'),
    ('quote_collect', '收藏/取消收藏', 'button'),
    ('quote_share', '分享语录', 'button'),
    ('quote_report', '举报语录', 'button'),
    ('quote_audio_play', '语录朗读播放', 'button'),
    ('home_scroll', '首页滑动翻页', 'other'),
    ('home_enter_ai', '首页进入 AI', 'button'),
    ('home_enter_vip', '首页进入会员', 'button'),
    # 新人喜欢引导（App 额外）
    ('newcomer_like_sheet_show', '新人喜欢引导展示', 'page'),
    ('newcomer_like_sheet_dismiss', '关闭新人喜欢引导', 'button'),
    ('newcomer_like_progress', '新人喜欢进度', 'other'),
    ('newcomer_like_complete', '新人喜欢完成', 'button'),
    # 9.4.4 AI
    ('ai_page_view', '打开 AI 页', 'page'),
    ('ai_quick_click', '点击 AI 快捷开场', 'button'),
    ('ai_send', 'AI 发送消息', 'button'),
    ('ai_success', 'AI 回复成功', 'other'),
    ('ai_fail', 'AI 生成失败', 'other'),
    ('ai_safety_block', 'AI 安全拒答', 'other'),
    ('ai_quota_exhaust', 'AI 额度用尽展示', 'page'),
    ('ai_get_free_quota_click', '点击获取免费次数', 'button'),
    ('ai_history_open', '打开 AI 历史', 'button'),
    # 9.4.5 签到
    ('checkin_show', '打卡页/弹层展示', 'page'),
    ('checkin_success', '打卡成功', 'button'),
    ('checkin_already', '当日已打卡', 'other'),
    ('checkin_freeze', '打卡爱心冻结', 'other'),
    ('checkin_reset', '连续打卡清零', 'other'),
    ('checkin_share', '分享打卡成就', 'button'),
    ('checkin_setting_toggle', '打卡设置开关', 'button'),
    # 9.4.6 会员付费
    ('vip_page_view', '进入会员页', 'page'),
    ('vip_popup_show', '会员弹窗曝光', 'page'),
    ('vip_plan_select', '选择会员套餐', 'button'),
    ('vip_pay_start', '发起商店支付', 'button'),
    ('vip_pay_success', '支付/订阅成功', 'button'),
    ('vip_pay_fail', '支付失败/取消', 'button'),
    ('vip_restore_click', '点击恢复购买', 'button'),
    ('vip_restore_result', '恢复购买结果', 'other'),
    ('vip_trial_start', '进入试用态', 'other'),
    # Manna / 免费产品兴趣：内容打开（params: content_type + content_id [+ title]）
    ('content_open', '内容打开', 'page'),
)

EVENT_LABELS = {name: label for name, label, _ in EVENT_CATALOG}
PAGE_EVENTS = {name for name, _, kind in EVENT_CATALOG if kind == 'page'}
BUTTON_EVENTS = {name for name, _, kind in EVENT_CATALOG if kind == 'button'}


def parse_date(value, default: date):
    """解析 YYYY-MM-DD；非法或空则返回 default。已是 date（非 datetime）原样返回。"""
    if not value:
        return default
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    s = str(value).strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return default


def parse_filters(params):
    """
    通用筛选：from/to/system_lang/register_country/platform/app_key；默认近 30 天（含今天共 30 日）。

    platform 仅认 ios/android/空；非法值置空（不过滤平台）。
    from > to 时自动对调。
    """
    today = dj_tz.now().date()
    d_to = parse_date(params.get('to'), today)
    d_from = parse_date(params.get('from'), d_to - timedelta(days=29))
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    lang = (params.get('system_lang') or '').strip()
    country = (params.get('register_country') or '').strip().upper()
    platform = (params.get('platform') or '').strip().lower()
    if platform not in ('ios', 'android', ''):
        platform = ''
    from tools.selah_quota import normalize_app_key
    app_key = normalize_app_key(params.get('app_key') or '')
    return d_from, d_to, lang, country, platform, app_key


def date_range_list(d_from: date, d_to: date):
    """闭区间日期列表（含起止），供序列补 0 用。"""
    days = []
    cur = d_from
    while cur <= d_to:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _user_base_qs(lang='', country='', platform='', app_key=''):
    """
    App 用户（role=user）基础 QuerySet，附带语言/国家/平台筛选。

    看板不统计后台账号（operator/admin/super_admin）。
    platform 对应 User.platform（ios/android）。
    """
    qs = User.objects.filter(role='user')
    if lang:
        qs = qs.filter(system_lang=lang)
    if country:
        qs = qs.filter(register_country__iexact=country)
    if platform:
        qs = qs.filter(platform=platform)
    if app_key:
        qs = qs.filter(app_key=app_key)
    return qs


def filtered_user_ids(lang='', country='', platform='', app_key=''):
    """满足筛选条件的用户 ID 列表（成交等需按用户维度关联时用）。"""
    return list(_user_base_qs(lang, country, platform, app_key).values_list('id', flat=True))


def apply_user_filters_to_events(qs, lang='', country='', platform='', app_key=''):
    """
    将语言/国家/平台/App 筛到 AnalyticsEvent 冗余字段上。

    埋点写入时已冗余 system_lang / register_country / platform / app_key，
    故不必 join User，利于大表聚合。
    """
    if lang:
        qs = qs.filter(system_lang=lang)
    if country:
        qs = qs.filter(register_country__iexact=country)
    if platform:
        qs = qs.filter(platform=platform)
    if app_key:
        qs = qs.filter(app_key=app_key)
    return qs


def closed_deal_qs(d_from, d_to, lang='', country='', platform='', app_key=''):
    """
    成交 QuerySet：区间内 amount_cents>0 且非退款/撤销/取消。

    时间：created_at ∈ [d_from 00:00 UTC, d_to+1 00:00 UTC)。
    用户维度筛选：若 lang/country/platform/app_key 任一非空，用 filtered_user_ids 限制 user_id。
    注意：elif platform 分支在当前 if 结构下不可达（platform 非空已进上一支）；
    保留作历史兼容意图——仅平台筛收据时可不经用户表。
    """
    start = datetime.combine(d_from, datetime.min.time(), tzinfo=dt_tz.utc)
    end = datetime.combine(d_to + timedelta(days=1), datetime.min.time(), tzinfo=dt_tz.utc)
    qs = IapReceipt.objects.filter(
        created_at__gte=start,
        created_at__lt=end,
        amount_cents__gt=0,
    ).exclude(status__in=('refunded', 'revoked', 'cancelled'))
    if lang or country or platform or app_key:
        uids = filtered_user_ids(lang, country, platform, app_key)
        qs = qs.filter(user_id__in=uids)
    elif platform:
        # 仅平台、无语言/国家时：直接按收据 platform（当前因上一条件含 platform 而难进入）
        qs = qs.filter(platform=platform)
    return qs


def series_dau(d_from, d_to, lang='', country='', platform='', app_key=''):
    """
    日活序列：按 User.last_active_at 落在当日计数；缺日补 0。

    DAU 数据源是用户表 last_active_at（登录/鉴权等路径更新），
    不是 AnalyticsEvent UV。与埋点「活跃」口径可能略有差异。
    """
    start = datetime.combine(d_from, datetime.min.time(), tzinfo=dt_tz.utc)
    end = datetime.combine(d_to + timedelta(days=1), datetime.min.time(), tzinfo=dt_tz.utc)
    qs = _user_base_qs(lang, country, platform, app_key).filter(
        last_active_at__gte=start,
        last_active_at__lt=end,
    )
    rows = (
        qs.annotate(day=TruncDate('last_active_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )
    by_day = {r['day']: r['count'] for r in rows if r['day']}
    return [{'date': d.isoformat(), 'count': by_day.get(d, 0)} for d in date_range_list(d_from, d_to)]


def series_new_users(d_from, d_to, lang='', country='', platform='', app_key=''):
    """新增用户日序列：按 User.created_at TruncDate；缺日补 0。"""
    start = datetime.combine(d_from, datetime.min.time(), tzinfo=dt_tz.utc)
    end = datetime.combine(d_to + timedelta(days=1), datetime.min.time(), tzinfo=dt_tz.utc)
    qs = _user_base_qs(lang, country, platform, app_key).filter(created_at__gte=start, created_at__lt=end)
    rows = (
        qs.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )
    by_day = {r['day']: r['count'] for r in rows if r['day']}
    return [{'date': d.isoformat(), 'count': by_day.get(d, 0)} for d in date_range_list(d_from, d_to)]


def series_revenue(d_from, d_to, lang='', country='', platform='', app_key=''):
    """
    收入日序列（成交口径 closed_deal_qs）。

    每日字段：
    - amount_cents：成交金额合计（分）
    - order_count：成交笔数
    - payer_count：当日去重付费用户数
    """
    qs = closed_deal_qs(d_from, d_to, lang, country, platform, app_key)
    rows = (
        qs.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(
            amount_cents=Sum('amount_cents'),
            order_count=Count('id'),
            payer_count=Count('user_id', distinct=True),
        )
        .order_by('day')
    )
    by_day = {r['day']: r for r in rows if r['day']}
    out = []
    for d in date_range_list(d_from, d_to):
        r = by_day.get(d) or {}
        out.append({
            'date': d.isoformat(),
            'amount_cents': int(r.get('amount_cents') or 0),
            'order_count': int(r.get('order_count') or 0),
            'payer_count': int(r.get('payer_count') or 0),
        })
    return out


def series_orders_kpi(d_from, d_to, lang='', country='', platform='', app_key=''):
    """
    订单 KPI 日序列。

    - pay_rate = payer_count / DAU（当日无 DAU 则为 0）
    - arppu_cents = amount_cents / payer_count（无付费者为 0）
      单位：分/付费人；非「每活跃用户收入 ARPU」
    """
    dau = {x['date']: x['count'] for x in series_dau(d_from, d_to, lang, country, platform, app_key)}
    rev = series_revenue(d_from, d_to, lang, country, platform, app_key)
    out = []
    for r in rev:
        d = r['date']
        dau_n = dau.get(d, 0) or 0
        payers = r['payer_count']
        amount = r['amount_cents']
        pay_rate = (payers / dau_n) if dau_n else 0.0
        arppu = (amount / payers) if payers else 0.0
        out.append({
            'date': d,
            'order_count': r['order_count'],
            'payer_count': payers,
            'dau': dau_n,
            'pay_rate': round(pay_rate, 6),
            'amount_cents': amount,
            'arppu_cents': round(arppu, 2),
        })
    return out


def retention_table(d_from, d_to, lang='', country='', platform='', app_key='', max_day=30):
    """
    按注册日 cohort，列出 D0–D30 留存率。

    定义：
    - cohort：created_at 落在 [d_from, d_to] 的 App 用户，按注册日分组
    - Dn 活跃：注册日 + n 那天，满足其一即可
        1) last_active_at 的日历日 == 目标日
        2) AnalyticsEvent.event_date == 目标日（补 last_active 漏更）
    - rate = retained / cohort_size；目标日 > today 填 None（未观测）

    注意：D0 通常接近 100%（注册当天多半有活跃）；大 cohort 时内存压力随用户数上升。
    """
    start = datetime.combine(d_from, datetime.min.time(), tzinfo=dt_tz.utc)
    end = datetime.combine(d_to + timedelta(days=1), datetime.min.time(), tzinfo=dt_tz.utc)
    users = list(
        _user_base_qs(lang, country, platform, app_key)
        .filter(created_at__gte=start, created_at__lt=end)
        .values('id', 'created_at', 'last_active_at')
    )
    if not users:
        return []

    # 预取事件活跃日，避免按用户逐日查库
    uids = [u['id'] for u in users]
    event_days = defaultdict(set)
    ev_qs = AnalyticsEvent.objects.filter(
        user_id__in=uids,
        event_date__gte=d_from,
        event_date__lte=d_to + timedelta(days=max_day),
    ).values_list('user_id', 'event_date')
    for uid, ed in ev_qs.iterator():
        event_days[uid].add(ed)

    cohorts = defaultdict(list)
    for u in users:
        cday = u['created_at'].astimezone(dt_tz.utc).date() if u['created_at'].tzinfo else u['created_at'].date()
        cohorts[cday].append(u)

    today = dj_tz.now().date()
    result = []
    for cday in sorted(cohorts.keys()):
        members = cohorts[cday]
        size = len(members)
        row = {'cohort_date': cday.isoformat(), 'cohort_size': size, 'retention': []}
        for n in range(0, max_day + 1):
            target = cday + timedelta(days=n)
            if target > today:
                row['retention'].append({'day': n, 'retained': None, 'rate': None})
                continue
            retained = 0
            for u in members:
                active = False
                if u['last_active_at']:
                    ad = u['last_active_at'].astimezone(dt_tz.utc).date() if u['last_active_at'].tzinfo else u['last_active_at'].date()
                    if ad == target:
                        active = True
                if not active and target in event_days.get(u['id'], ()):
                    active = True
                if active:
                    retained += 1
            rate = (retained / size) if size else 0.0
            row['retention'].append({'day': n, 'retained': retained, 'rate': round(rate, 6)})
        result.append(row)
    return result


def geo_device_dist(lang='', country='', platform='', app_key=''):
    """
    国家与设备（platform）分布；空值归入 (unknown)。

    注意：若传入 country/platform 筛选，分布是「筛选后子集」内的再分组。
    """
    qs = _user_base_qs(lang, country, platform, app_key)
    countries = list(
        qs.exclude(register_country='')
        .values('register_country')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    # 空国家单独
    empty_c = qs.filter(Q(register_country='') | Q(register_country__isnull=True)).count()
    if empty_c:
        countries.append({'register_country': '(unknown)', 'count': empty_c})

    devices = list(
        qs.exclude(platform='')
        .values('platform')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    empty_p = qs.filter(Q(platform='') | Q(platform__isnull=True)).count()
    if empty_p:
        devices.append({'platform': '(unknown)', 'count': empty_p})
    return {'countries': countries, 'devices': devices}


def language_dist(lang='', country='', platform='', app_key=''):
    """system_lang 分布；空值记为 (unknown)。"""
    qs = _user_base_qs(lang, country, platform, app_key)
    rows = list(
        qs.values('system_lang')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    return [{'system_lang': r['system_lang'] or '(unknown)', 'count': r['count']} for r in rows]


def _event_kind(name: str) -> str:
    """目录外事件的 kind 启发式：page / button / other。"""
    if name in PAGE_EVENTS or name.endswith('_page_view') or '_enter_' in name:
        return 'page'
    if name in BUTTON_EVENTS or name.endswith('_click') or name.endswith('_select'):
        return 'button'
    return 'other'


def events_aggregate(d_from, d_to, lang='', country='', platform='', app_key=''):
    """
    按 §9.4 全量目录返回中文名 + UV/PV；无数据事件 uv/pv=0。

    - pv = Count(id)；uv = Count(user_id, distinct)
    - 目录外但库中存在的事件追加在后，kind 用启发式
    - 最终按 pv 降序、name 升序
    """
    qs = AnalyticsEvent.objects.filter(event_date__gte=d_from, event_date__lte=d_to)
    qs = apply_user_filters_to_events(qs, lang, country, platform, app_key)
    counted = {
        r['event_name']: {'uv': r['uv'], 'pv': r['pv']}
        for r in qs.values('event_name').annotate(
            pv=Count('id'), uv=Count('user_id', distinct=True),
        )
    }

    out = []
    seen = set()
    for name, label, kind in EVENT_CATALOG:
        seen.add(name)
        c = counted.get(name) or {'uv': 0, 'pv': 0}
        out.append({
            'event_name': name,
            'event_label': label,
            'kind': kind,
            'uv': c['uv'],
            'pv': c['pv'],
        })
    # 库中出现但不在目录里的事件一并带上
    for name, c in counted.items():
        if name in seen:
            continue
        out.append({
            'event_name': name,
            'event_label': EVENT_LABELS.get(name) or name,
            'kind': _event_kind(name),
            'uv': c['uv'],
            'pv': c['pv'],
        })
    out.sort(key=lambda x: (-x['pv'], x['event_name']))
    return out


def overview(d_from, d_to, lang='', country='', platform='', app_key=''):
    """
    看板总览：区间 KPI 汇总 + dau/new_users/revenue/orders_kpi 序列。

    KPI 说明：
    - unique_active：区间内 last_active_at 命中的去重用户（真·区间活跃）
    - sum_daily_dau：每日 DAU 之和（同一用户多天会重复累计）
    - pay_rate：total_payers / unique_active
    - arppu_cents：total_amount / total_payers（分/付费人）
    """
    dau = series_dau(d_from, d_to, lang, country, platform, app_key)
    news = series_new_users(d_from, d_to, lang, country, platform, app_key)
    rev = series_revenue(d_from, d_to, lang, country, platform, app_key)
    kpi = series_orders_kpi(d_from, d_to, lang, country, platform, app_key)

    total_dau = sum(x['count'] for x in dau)
    # 区间去重 DAU 近似：用 last_active 落在区间的用户数
    start = datetime.combine(d_from, datetime.min.time(), tzinfo=dt_tz.utc)
    end = datetime.combine(d_to + timedelta(days=1), datetime.min.time(), tzinfo=dt_tz.utc)
    unique_active = _user_base_qs(lang, country, platform, app_key).filter(
        last_active_at__gte=start, last_active_at__lt=end,
    ).count()
    total_new = sum(x['count'] for x in news)
    total_amount = sum(x['amount_cents'] for x in rev)
    total_orders = sum(x['order_count'] for x in rev)
    total_payers = closed_deal_qs(d_from, d_to, lang, country, platform, app_key).values('user_id').distinct().count()
    avg_pay_rate = (total_payers / unique_active) if unique_active else 0.0
    arppu = (total_amount / total_payers) if total_payers else 0.0

    return {
        'from': d_from.isoformat(),
        'to': d_to.isoformat(),
        'kpi': {
            'unique_active': unique_active,
            'sum_daily_dau': total_dau,
            'new_users': total_new,
            'amount_cents': total_amount,
            'order_count': total_orders,
            'payer_count': total_payers,
            'pay_rate': round(avg_pay_rate, 6),
            'arppu_cents': round(arppu, 2),
        },
        'series': {
            'dau': dau,
            'new_users': news,
            'revenue': rev,
            'orders_kpi': kpi,
        },
    }


TASK_LEDGER_REASONS = ('prayer_amen', 'devotion_amen', 'bible_read', 'quiz_pass')
PRACTICE_FLAG_KEYS = ('prayer', 'morning', 'night', 'devotion', 'quiz')


def _activity_is_practice(row):
    if not row:
        return False
    if int(row.pray_sec or 0) or int(row.devotion_sec or 0) or int(row.quiz_count or 0):
        return True
    if int(row.read_sec or 0) >= 300:
        return True
    flags = row.flags if isinstance(row.flags, dict) else {}
    return any(bool(flags.get(k)) for k in PRACTICE_FLAG_KEYS)


def _pct(num, den):
    if not den:
        return 0.0
    return round(float(num) / float(den), 6)


def manna_practice(d_from, d_to, lang='', country='', platform='', app_key=''):
    """Manna 功课留存 KPI：账本 + 活动日 + 测验局，不用客户端埋点。"""
    app = app_key or 'manna'
    users = _user_base_qs(lang, country, platform, app)
    user_ids = list(users.values_list('id', flat=True))
    start = datetime.combine(d_from, datetime.min.time(), tzinfo=dt_tz.utc)
    end = datetime.combine(d_to + timedelta(days=1), datetime.min.time(), tzinfo=dt_tz.utc)
    now = dj_tz.now()

    new_rows = list(
        users.filter(created_at__gte=start, created_at__lt=end).values('id', 'created_at')
    )
    new_ids = [r['id'] for r in new_rows]
    n_new = len(new_ids)
    grant_uids = set()
    if new_ids:
        grant_uids = set(
            MannaLedger.objects.filter(
                app_key=app, reason='grant_newcomer', user_id__in=new_ids,
            ).values_list('user_id', flat=True)
        )
    created_day = {}
    for r in new_rows:
        dt = r['created_at']
        if dt is None:
            continue
        if getattr(dt, 'tzinfo', None):
            dt = dt.astimezone(dt_tz.utc)
        created_day[r['id']] = dt.date()

    first_task = 0
    if grant_uids:
        ledgers = MannaLedger.objects.filter(
            app_key=app, user_id__in=list(grant_uids), reason__in=TASK_LEDGER_REASONS,
        ).values('user_id', 'created_at')
        seen = set()
        for row in ledgers:
            uid = row['user_id']
            if uid in seen:
                continue
            d0 = created_day.get(uid)
            dt = row['created_at']
            if not d0 or not dt:
                continue
            if getattr(dt, 'tzinfo', None):
                dt = dt.astimezone(dt_tz.utc)
            if dt.date() == d0:
                seen.add(uid)
        first_task = len(seen)

    d1_ok = 0
    d7_ok = 0
    if created_day:
        days_needed = set()
        d1_map = {}
        d7_map = {}
        for uid, d0 in created_day.items():
            d1_map[uid] = d0 + timedelta(days=1)
            d7_map[uid] = d0 + timedelta(days=7)
            days_needed.add(d1_map[uid])
            days_needed.add(d7_map[uid])
        act_rows = ActivityDay.objects.filter(
            app_key=app, user_id__in=list(created_day.keys()), date__in=list(days_needed),
        )
        practice_keys = set()
        for row in act_rows.iterator():
            if _activity_is_practice(row):
                practice_keys.add((row.user_id, row.date))
        for uid in created_day:
            if (uid, d1_map[uid]) in practice_keys:
                d1_ok += 1
            if (uid, d7_map[uid]) in practice_keys:
                d7_ok += 1

    dau = series_dau(d_from, d_to, lang, country, platform, app)
    dau_map = {x['date']: int(x['count'] or 0) for x in dau}
    practice_by_day = defaultdict(set)
    for row in ActivityDay.objects.filter(
        app_key=app, date__gte=d_from, date__lte=d_to, user_id__in=user_ids or [0],
    ).iterator():
        if _activity_is_practice(row):
            practice_by_day[row.date.isoformat()].add(row.user_id)
    daily = []
    practice_uv = set()
    for d in date_range_list(d_from, d_to):
        key = d.isoformat()
        pset = practice_by_day.get(key) or set()
        practice_uv |= pset
        daily.append({
            'date': key,
            'dau': dau_map.get(key, 0),
            'practice_dau': len(pset),
        })
    sum_dau = sum(x['dau'] for x in daily) or 0
    sum_practice = sum(x['practice_dau'] for x in daily)

    att = MannaQuizAttempt.objects.filter(
        app_key=app, user_id__in=user_ids or [0],
        started_at__gte=start, started_at__lt=end,
    )
    started_n = att.count()
    completed_n = att.filter(status=MannaQuizAttempt.STATUS_COMPLETED).count()
    passed_n = att.filter(status=MannaQuizAttempt.STATUS_COMPLETED, manna_delta__gt=0).count()

    fail_qs = MannaQuizAttempt.objects.filter(
        app_key=app, user_id__in=user_ids or [0],
        status=MannaQuizAttempt.STATUS_COMPLETED, manna_delta__lt=0,
        finished_at__gte=start, finished_at__lt=end,
    )
    fail_n = fail_qs.count()
    retried = 0
    for f in fail_qs.only('id', 'user_id', 'level_id', 'finished_at').iterator():
        if not f.finished_at:
            continue
        if MannaQuizAttempt.objects.filter(
            user_id=f.user_id, level_id=f.level_id, app_key=app,
            started_at__gt=f.finished_at,
            started_at__lte=f.finished_at + timedelta(hours=24),
        ).exclude(id=f.id).exists():
            retried += 1

    led_qs = MannaLedger.objects.filter(
        app_key=app, user_id__in=user_ids or [0],
        created_at__gte=start, created_at__lt=end,
    )
    pos = led_qs.filter(delta__gt=0).aggregate(s=Sum('delta'))['s'] or 0
    neg = led_qs.filter(delta__lt=0).aggregate(s=Sum('delta'))['s'] or 0
    earn_spend = round(float(pos) / abs(float(neg)), 4) if neg else (float(pos) if pos else 0.0)

    cutoff = now - timedelta(days=7)
    old_ids = list(users.filter(created_at__lt=cutoff).values_list('id', flat=True))
    zero_n = 0
    old_n = len(old_ids)
    if old_ids:
        zero_n = MannaWallet.objects.filter(
            app_key=app, user_id__in=old_ids, balance=0,
        ).count()

    dups = (
        led_qs.filter(delta__gt=0)
        .values('user_id', 'reason', 'ref_id')
        .annotate(n=Count('id'))
        .filter(n__gt=1)
    )
    duplicate_grants = sum(int(x['n']) - 1 for x in dups)

    return {
        'from': d_from.isoformat(),
        'to': d_to.isoformat(),
        'app_key': app,
        'kpi': {
            'new_users': n_new,
            'd0_grant_rate': _pct(len(grant_uids), n_new),
            'd0_grant_users': len(grant_uids),
            'd0_first_task_rate': _pct(first_task, len(grant_uids) or n_new),
            'd0_first_task_users': first_task,
            'practice_dau_rate': _pct(sum_practice, sum_dau),
            'practice_uv': len(practice_uv),
            'd1_practice_retention': _pct(d1_ok, n_new),
            'd1_practice_users': d1_ok,
            'd7_practice_retention': _pct(d7_ok, n_new),
            'd7_practice_users': d7_ok,
            'quiz_started': started_n,
            'quiz_completed': completed_n,
            'quiz_settle_rate': _pct(completed_n, started_n),
            'quiz_pass_rate': _pct(passed_n, completed_n),
            'quiz_passed': passed_n,
            'quiz_fail': fail_n,
            'fail_retry_rate': _pct(retried, fail_n),
            'fail_retried': retried,
            'earn_spend_ratio': earn_spend,
            'zero_balance_share': _pct(zero_n, old_n),
            'zero_balance_users': zero_n,
            'duplicate_grants': duplicate_grants,
        },
        'daily': daily,
    }


CONTENT_OPEN_EVENT = 'content_open'
CONTENT_OPEN_TYPES = (
    'daily_verse',
    'prayer',
    'devotion_series',
    'devotion_session',
    'bible_chapter',
    'quiz_level',
    'quiz_story',
    'wallpaper',
    'quote',
)


def manna_content_opens(
    d_from, d_to, lang='', country='', platform='', app_key='',
    *, content_type='', limit=50,
):
    """按 content_type×content_id 聚合内容打开 PV/UV（AnalyticsEvent content_open）。

    参数约定（params JSON）:
      content_type / content_id 必填；title 可选；source=server|client。
    """
    from django.db.models.fields.json import KeyTextTransform

    app = app_key or 'manna'
    try:
        limit = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        limit = 50
    ctype_filter = (content_type or '').strip()

    qs = AnalyticsEvent.objects.filter(
        event_name=CONTENT_OPEN_EVENT,
        event_date__gte=d_from,
        event_date__lte=d_to,
    )
    qs = apply_user_filters_to_events(qs, lang, country, platform, app)
    qs = qs.annotate(
        content_type=KeyTextTransform('content_type', 'params'),
        content_id=KeyTextTransform('content_id', 'params'),
    ).exclude(content_type__isnull=True).exclude(content_type='').exclude(
        content_id__isnull=True,
    ).exclude(content_id='')
    if ctype_filter:
        qs = qs.filter(content_type=ctype_filter)

    by_type = list(
        qs.values('content_type').annotate(
            pv=Count('id'), uv=Count('user_id', distinct=True),
        ).order_by('-pv', 'content_type')
    )
    items_qs = qs.values('content_type', 'content_id').annotate(
        pv=Count('id'), uv=Count('user_id', distinct=True),
    ).order_by('-pv', 'content_type', 'content_id')[:limit]
    items = []
    for row in items_qs:
        items.append({
            'content_type': row['content_type'] or '',
            'content_id': str(row['content_id'] or ''),
            'title': '',
            'pv': int(row['pv'] or 0),
            'uv': int(row['uv'] or 0),
        })
    _fill_content_open_titles(items)

    total_pv = sum(int(x['pv'] or 0) for x in by_type)
    total_uv = AnalyticsEvent.objects.filter(
        event_name=CONTENT_OPEN_EVENT,
        event_date__gte=d_from,
        event_date__lte=d_to,
    )
    total_uv = apply_user_filters_to_events(total_uv, lang, country, platform, app)
    if ctype_filter:
        total_uv = total_uv.annotate(
            content_type=KeyTextTransform('content_type', 'params'),
        ).filter(content_type=ctype_filter)
    total_uv_n = total_uv.values('user_id').distinct().count()

    return {
        'from': d_from.isoformat(),
        'to': d_to.isoformat(),
        'app_key': app,
        'content_type': ctype_filter,
        'limit': limit,
        'kpi': {
            'total_pv': total_pv,
            'total_uv': total_uv_n,
            'types': len(by_type),
        },
        'by_type': [
            {
                'content_type': r['content_type'] or '',
                'pv': int(r['pv'] or 0),
                'uv': int(r['uv'] or 0),
            }
            for r in by_type
        ],
        'items': items,
    }


def _fill_content_open_titles(items):
    """用运营内容表补 title；查不到则保留空（客户端 title 亦可写入 params）。"""
    if not items:
        return
    from models.models import (
        DailyVerse, DevotionSeries, DevotionSession, MannaQuizLevel, MannaQuizStory, Wallpaper,
        Quote,
    )

    need = defaultdict(set)
    for it in items:
        need[it['content_type']].add(it['content_id'])

    titles = {}

    def _put(ctype, cid, title):
        if title:
            titles[(ctype, str(cid))] = str(title)[:200]

    # daily_verse / prayer 共用 DailyVerse.id
    verse_ids = set()
    for cid in need.get('daily_verse', set()) | need.get('prayer', set()):
        if str(cid).isdigit():
            verse_ids.add(int(cid))
    if verse_ids:
        for r in DailyVerse.objects.filter(id__in=verse_ids).only('id', 'reference', 'prayer_title'):
            _put('daily_verse', r.id, r.reference or r.prayer_title or f'verse#{r.id}')
            _put('prayer', r.id, (r.prayer_title or r.reference or f'prayer#{r.id}'))

    for ctype, Model, field in (
        ('devotion_series', DevotionSeries, 'title'),
        ('devotion_session', DevotionSession, 'title'),
        ('quiz_level', MannaQuizLevel, 'title'),
        ('quiz_story', MannaQuizStory, 'title'),
    ):
        ids = {int(x) for x in need.get(ctype, set()) if str(x).isdigit()}
        if not ids:
            continue
        for r in Model.objects.filter(id__in=ids).only('id', field):
            _put(ctype, r.id, getattr(r, field, '') or f'{ctype}#{r.id}')

    wp_ids = {int(x) for x in need.get('wallpaper', set()) if str(x).isdigit()}
    if wp_ids:
        for r in Wallpaper.objects.filter(id__in=wp_ids).only('id', 'name_en', 'theme'):
            _put('wallpaper', r.id, r.name_en or r.theme or f'wallpaper#{r.id}')

    q_ids = {int(x) for x in need.get('quote', set()) if str(x).isdigit()}
    if q_ids:
        for r in Quote.objects.filter(id__in=q_ids).only('id', 'content', 'source_ref'):
            text = (r.source_ref or (r.content or '')[:40] or f'quote#{r.id}')
            _put('quote', r.id, text)

    for it in items:
        if it['content_type'] == 'bible_chapter' and not it['title']:
            it['title'] = it['content_id']
            continue
        key = (it['content_type'], it['content_id'])
        if key in titles:
            it['title'] = titles[key]

