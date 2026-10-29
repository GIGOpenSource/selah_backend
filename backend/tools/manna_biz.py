#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Manna 业务工具：钱包、活动日、今日经文挑选（与 Selah AI quota / checkin 隔离）。"""
import hashlib
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone as dj_tz

from urllib.parse import unquote

from models.models import (
    ActivityDay, BadgeDef, BadgeUser, BibleAmen, DailyVerse, DevotionProgress,
    MannaLedger, MannaQuizAttempt, MannaQuizLevel, MannaQuizStoryUnlock, MannaWallet,
    PrayerContent, VerseCardTheme,
)
from tools.selah_quota import (
    MANNA_APP_KEY,
    ensure_manna_defaults,
    filter_scoped_rows,
    get_config,
    get_config_context,
    get_manna_wallet_snapshot,
    normalize_app_key,
    normalize_lang,
    normalize_platform,
    scope_score,
    set_config_context,
)

logger = logging.getLogger(__name__)

REASON_GRANT_NEWCOMER = 'grant_newcomer'
REASON_PRAYER_AMEN = 'prayer_amen'
REASON_DEVOTION_AMEN = 'devotion_amen'
REASON_BIBLE_READ = 'bible_read'
REASON_QUIZ_PASS = 'quiz_pass'
WALLET_FLAG_NEWCOMER = 'newcomer_granted'

# 成长卡四格：各行为终身获得（不含新人赠币）
TASK_EARN_REASONS = (
    REASON_PRAYER_AMEN, REASON_DEVOTION_AMEN, REASON_BIBLE_READ, REASON_QUIZ_PASS,
)
# 今日收益：四行为 + 新人赠币
TODAY_EARN_REASONS = TASK_EARN_REASONS + (REASON_GRANT_NEWCOMER,)


def count_manna_ledger_reason(user, app_key, reason):
    """成功入账笔数（与流水同步；Amen 每种 kind/日最多一条）。"""
    app = _manna_app(app_key if app_key is not None else getattr(user, 'app_key', None))
    return MannaLedger.objects.filter(
        user_id=user.id, app_key=app, reason=reason,
    ).count()


def sum_manna_ledger(user, app_key, reasons, *, created_gte=None, created_lt=None):
    """按 reason 汇总 delta（可为负）；reasons 为空返回 0。"""
    app = _manna_app(app_key if app_key is not None else getattr(user, 'app_key', None))
    reasons = [r for r in (reasons or ()) if r]
    if not reasons:
        return 0
    qs = MannaLedger.objects.filter(
        user_id=user.id, app_key=app, reason__in=reasons,
    )
    if created_gte is not None:
        qs = qs.filter(created_at__gte=created_gte)
    if created_lt is not None:
        qs = qs.filter(created_at__lt=created_lt)
    return int(qs.aggregate(s=Sum('delta'))['s'] or 0)


def sum_manna_ledger_by_reason(user, app_key, reasons):
    """返回 {reason: sum(delta)}，未出现的 reason 为 0。"""
    app = _manna_app(app_key if app_key is not None else getattr(user, 'app_key', None))
    reasons = [r for r in (reasons or ()) if r]
    out = {r: 0 for r in reasons}
    if not reasons:
        return out
    rows = (
        MannaLedger.objects.filter(
            user_id=user.id, app_key=app, reason__in=reasons,
        )
        .values('reason')
        .annotate(s=Sum('delta'))
    )
    for row in rows:
        out[str(row['reason'])] = int(row['s'] or 0)
    return out


def local_day_utc_range(local_day, tz_name):
    """用户本地自然日 [start, next) 对应的 UTC aware datetime。"""
    if isinstance(local_day, str):
        local_day = date.fromisoformat(local_day[:10])
    tz = ZoneInfo(resolve_tz_name(tz_name))
    start_local = datetime.combine(local_day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(ZoneInfo('UTC')), end_local.astimezone(ZoneInfo('UTC'))


# 日历「功课日」：祷告/灵修/答题 flag；读经靠时长，不单靠 Amen
PRACTICE_FLAGS = frozenset({
    'prayer', 'morning', 'night', 'devotion', 'quiz',
})
READ_ACTIVE_SEC = 60

REASON_QUIZ_IN_BIBLE = 'quiz_in_bible'
REASON_QUIZ_REVIEW = 'quiz_review'


def annotate_story_progress(stories, unlocked_ids):
    """故事卡按 sort_order 顺序学：学完上一张才可学下一张。unlocked=已学完（有 unlock 行），不看 unlock_cost。"""
    done_ids = set(unlocked_ids or [])
    prev_done = True
    out = {}
    for s in stories:
        sid = getattr(s, 'id', None)
        if sid is None:
            continue
        done = sid in done_ids
        available = bool(done or prev_done)
        out[sid] = {
            'unlocked': done,
            'available': available,
            'locked': not available,
        }
        prev_done = done
    return out


STORY_BLOCKS_IMAGE_MAX = 6


def normalize_story_blocks(raw_blocks, cover_image_index=0, *, require_image=False):
    """校验并规范化故事图文块。

    返回 (blocks, cover_image_index, cover_url, body, error) ；error 非空表示失败。
    blocks 元素：{"type":"text","text":"..."} 或 {"type":"image","url":"...","alt":"...?"}
    """
    if raw_blocks is None or raw_blocks == '':
        raw_blocks = []
    if not isinstance(raw_blocks, list):
        return None, 0, '', '', 'blocks must be list'
    blocks = []
    image_urls = []
    for i, item in enumerate(raw_blocks):
        if not isinstance(item, dict):
            return None, 0, '', '', f'blocks[{i}] must be object'
        kind = str(item.get('type') or '').strip().lower()
        if kind == 'text':
            text = str(item.get('text') or '').strip()
            if not text:
                return None, 0, '', '', f'blocks[{i}].text required'
            blocks.append({'type': 'text', 'text': text})
        elif kind == 'image':
            url = str(item.get('url') or '').strip()
            if not url:
                return None, 0, '', '', f'blocks[{i}].url required'
            row = {'type': 'image', 'url': url}
            alt = str(item.get('alt') or '').strip()
            if alt:
                row['alt'] = alt
            blocks.append(row)
            image_urls.append(url)
        else:
            return None, 0, '', '', f'blocks[{i}].type must be text or image'
    if len(image_urls) > STORY_BLOCKS_IMAGE_MAX:
        return None, 0, '', '', f'blocks image count max {STORY_BLOCKS_IMAGE_MAX}'
    if require_image and not image_urls:
        return None, 0, '', '', 'blocks need at least 1 image'
    try:
        idx = int(cover_image_index if cover_image_index is not None else 0)
    except (TypeError, ValueError):
        return None, 0, '', '', 'cover_image_index invalid'
    if idx < 0:
        return None, 0, '', '', 'cover_image_index invalid'
    cover_url = ''
    if image_urls:
        if idx >= len(image_urls):
            return None, 0, '', '', 'cover_image_index out of range'
        cover_url = image_urls[idx]
    elif idx != 0:
        return None, 0, '', '', 'cover_image_index out of range'
    body = '\n\n'.join(b['text'] for b in blocks if b.get('type') == 'text')
    return blocks, idx, cover_url, body, None


def story_blocks_for_client(story):
    """详情返回用：有 blocks 用 blocks；否则把旧 body 当成单段文字。"""
    blocks = getattr(story, 'blocks', None) or []
    if isinstance(blocks, list) and blocks:
        return blocks
    body = (getattr(story, 'body', None) or '').strip()
    if body:
        return [{'type': 'text', 'text': body}]
    return []


def _manna_app(app_key=None):
    app = normalize_app_key(app_key or '') or MANNA_APP_KEY
    return app


def ensure_manna_wallet(user, app_key=None):
    """确保钱包行存在；新人赠币只发一次（ledger reason=grant_newcomer）。"""
    ensure_manna_defaults()
    app = _manna_app(app_key if app_key is not None else getattr(user, 'app_key', None))
    with transaction.atomic():
        wallet, _created = MannaWallet.objects.select_for_update().get_or_create(
            user_id=user.id,
            app_key=app,
            defaults={'balance': 0, 'stats': {}},
        )
        stats = wallet.stats if isinstance(wallet.stats, dict) else {}
        already = bool(stats.get(WALLET_FLAG_NEWCOMER))
        if not already:
            already = MannaLedger.objects.filter(
                user_id=user.id, app_key=app, reason=REASON_GRANT_NEWCOMER,
            ).exists()
        if already:
            if not stats.get(WALLET_FLAG_NEWCOMER):
                stats[WALLET_FLAG_NEWCOMER] = True
                wallet.stats = stats
                wallet.save(update_fields=['stats', 'updated_at'])
            return wallet

        # 读 manna scope 配置
        set_config_context(app_key=app, language='', platform='')
        grant = int(get_config('manna_newcomer_grant', 200) or 0)
        if grant > 0:
            wallet.balance = int(wallet.balance or 0) + grant
            MannaLedger.objects.create(
                user_id=user.id,
                app_key=app,
                delta=grant,
                balance_after=wallet.balance,
                reason=REASON_GRANT_NEWCOMER,
                ref_type='wallet',
                ref_id=str(wallet.id),
                remark='newcomer grant',
            )
        stats[WALLET_FLAG_NEWCOMER] = True
        wallet.stats = stats
        wallet.save(update_fields=['balance', 'stats', 'updated_at'])
        logger.info('manna newcomer grant user=%s app=%s delta=%s', user.id, app, grant)
        return wallet


def apply_manna_delta(user, app_key, delta, reason, ref_type='', ref_id='', floor_zero=True, remark=''):
    """在事务内加减余额并写流水；floor_zero 时余额不低于 0。"""
    app = _manna_app(app_key)
    delta = int(delta or 0)
    try:
        with transaction.atomic():
            wallet, _ = MannaWallet.objects.select_for_update().get_or_create(
                user_id=user.id,
                app_key=app,
                defaults={'balance': 0, 'stats': {}},
            )
            bal = int(wallet.balance or 0) + delta
            if floor_zero and bal < 0:
                bal = 0
            wallet.balance = bal
            wallet.save(update_fields=['balance', 'updated_at'])
            MannaLedger.objects.create(
                user_id=user.id,
                app_key=app,
                delta=delta,
                balance_after=bal,
                reason=str(reason or '')[:64],
                ref_type=str(ref_type or '')[:32],
                ref_id=str(ref_id or '')[:64],
                remark=str(remark or '')[:255],
            )
            return wallet
    except IntegrityError:
        return MannaWallet.objects.filter(user_id=user.id, app_key=app).first()


def touch_activity_day(user, app_key, day=None, **kwargs):
    """累加活动日时长/计数/flags。

    支持 kwargs: pray_sec, devotion_sec, read_sec, quiz_count, flags(dict merge)。
    """
    app = _manna_app(app_key if app_key is not None else getattr(user, 'app_key', None))
    if day is None:
        day = dj_tz.now().date()
    elif isinstance(day, str):
        day = date.fromisoformat(day)
    with transaction.atomic():
        row, _ = ActivityDay.objects.select_for_update().get_or_create(
            user_id=user.id,
            app_key=app,
            date=day,
            defaults={
                'flags': {},
                'pray_sec': 0,
                'devotion_sec': 0,
                'read_sec': 0,
                'quiz_count': 0,
            },
        )
        for key in ('pray_sec', 'devotion_sec', 'read_sec', 'quiz_count'):
            if key in kwargs and kwargs[key] is not None:
                setattr(row, key, int(getattr(row, key) or 0) + int(kwargs[key] or 0))
        flags = kwargs.get('flags')
        if isinstance(flags, dict) and flags:
            cur = row.flags if isinstance(row.flags, dict) else {}
            cur.update(flags)
            row.flags = cur
        row.save()
        # 同步钱包 stats 缓存（四维）
        wallet, _ = MannaWallet.objects.get_or_create(
            user_id=user.id, app_key=app, defaults={'balance': 0, 'stats': {}},
        )
        agg = ActivityDay.objects.filter(user_id=user.id, app_key=app)
        from django.db.models import Sum
        totals = agg.aggregate(
            pray=Sum('pray_sec'), devotion=Sum('devotion_sec'),
            read=Sum('read_sec'), quiz=Sum('quiz_count'),
        )
        stats = wallet.stats if isinstance(wallet.stats, dict) else {}
        stats.update({
            'pray_sec': int(totals['pray'] or 0),
            'devotion_sec': int(totals['devotion'] or 0),
            'read_sec': int(totals['read'] or 0),
            'quiz_count': int(totals['quiz'] or 0),
        })
        wallet.stats = stats
        wallet.save(update_fields=['stats', 'updated_at'])
        return row


_ROTATE_MODES = frozenset({'date', 'calendar', 'daily_random', 'sort', 'fixed'})
_ROTATE_ALIASES = {
    'active_date': 'date',
    'rotate': 'calendar',
    'random': 'daily_random',
}


def _rotate_mode(key, default='date'):
    raw = str(get_config(key, default) or default).strip().lower().replace('-', '_')
    raw = _ROTATE_ALIASES.get(raw, raw)
    return raw if raw in _ROTATE_MODES else default


def _config_int(key, default=0):
    try:
        return int(get_config(key, default) or 0)
    except (TypeError, ValueError):
        return int(default or 0)


def _pick_from_pool(pool, mode, *, fixed_id=0, seed=''):
    """从已收窄的内容池挑一条。daily_random 按日历日哈希，当天内稳定。"""
    if not pool:
        return None
    if mode == 'fixed':
        fid = int(fixed_id or 0)
        if fid:
            for row in pool:
                if int(row.id) == fid:
                    return row
        mode = 'date'
    if mode == 'sort':
        return sorted(pool, key=lambda r: (int(r.sort_order or 0), r.id))[0]
    day = date.today().toordinal()
    if mode == 'daily_random':
        blob = f'{day}|{seed}|' + ','.join(str(r.id) for r in pool)
        idx = int(hashlib.md5(blob.encode()).hexdigest(), 16) % len(pool)
        return pool[idx]
    return pool[day % len(pool)]


def normalize_versecard_image_url(url):
    """分享底图 URL 对齐：去空白、有限次 decode，便于匹配已有主题。"""
    text = str(url or '').strip()
    for _ in range(4):
        if '%' not in text:
            break
        try:
            decoded = unquote(text)
        except Exception:
            break
        if decoded == text:
            break
        text = decoded
    return text.rstrip('/')


def serialize_versecard_theme(row):
    if not row:
        return None
    return {
        'key': row.theme_key,
        'image_url': row.image_url or '',
        'is_default': bool(row.is_default),
        'sort_order': int(row.sort_order or 0),
    }


def list_versecard_themes(app_key, language, platform, *, enabled_only=True):
    """可见主题列表；同 key 取作用域分最高的一行，同图 URL 只保留先出现的。"""
    app = _manna_app(app_key)
    lang = normalize_lang(language or '')
    plat = normalize_platform(platform or '')
    qs = VerseCardTheme.objects.filter(app_key=app)
    if enabled_only:
        qs = qs.filter(enabled=True)
    if lang:
        qs = qs.filter(Q(language='') | Q(language=lang))
    if plat:
        qs = qs.filter(Q(platform='') | Q(platform=plat))
    rows = list(qs.order_by('sort_order', 'id')[:200])
    scoped = filter_scoped_rows(rows, lang, plat, group_key='theme_key', app_key=app)
    seen_urls = set()
    out = []
    default_key = ''
    for row in scoped:
        url = normalize_versecard_image_url(row.image_url)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        out.append(row)
        if row.is_default and not default_key:
            default_key = row.theme_key
    if not default_key and out:
        default_key = out[0].theme_key
    return out, default_key


def versecard_themes_payload(app_key, language, platform, *, bg='', theme_key=''):
    """App 底栏 + 选中态。命中已有图则只回 theme_key，不另插 session。"""
    rows, default_key = list_versecard_themes(app_key, language, platform)
    themes = [serialize_versecard_theme(r) for r in rows]
    keys = {r.theme_key for r in rows}
    chosen = str(theme_key or '').strip()
    incoming = normalize_versecard_image_url(bg)
    if chosen and chosen not in keys:
        chosen = ''
    if not chosen and incoming:
        for row in rows:
            if normalize_versecard_image_url(row.image_url) == incoming:
                chosen = row.theme_key
                incoming = ''
                break
    if not chosen:
        chosen = default_key
    return {
        'themes': themes,
        'default_key': default_key,
        'theme_key': chosen,
        'incoming_bg': incoming if incoming and incoming not in {
            normalize_versecard_image_url(r.image_url) for r in rows
        } else '',
    }


def find_versecard_theme_url_conflict(app_key, language, platform, image_url, exclude_id=None):
    """同三维下 image_url 规范化后冲突的已有行。"""
    want = normalize_versecard_image_url(image_url)
    if not want:
        return None
    app = _manna_app(app_key)
    lang = normalize_lang(language or '')
    plat = normalize_platform(platform or '')
    qs = VerseCardTheme.objects.filter(app_key=app, language=lang, platform=plat)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    for row in qs:
        if normalize_versecard_image_url(row.image_url) == want:
            return row
    return None


def pick_daily_verse(app_key, language, platform):
    """按运营配置轮换：默认今日 active_date，否则最高 sort。语言/平台在 SQL 侧收窄。"""
    app = _manna_app(app_key)
    lang = normalize_lang(language or '')
    plat = normalize_platform(platform or '')
    today = dj_tz.now().date()
    mode = _rotate_mode('manna_verse_rotate', 'date')
    qs = DailyVerse.objects.filter(enabled=True, app_key=app)
    if lang:
        qs = qs.filter(Q(language='') | Q(language=lang))
    if plat:
        qs = qs.filter(Q(platform='') | Q(platform=plat))
    rows = list(qs.order_by('sort_order', 'id')[:200])
    scoped = filter_scoped_rows(rows, lang, plat, group_key=None, app_key=app)
    if not scoped:
        return None
    if mode == 'date':
        today_rows = [r for r in scoped if r.active_date == today]
        if today_rows:
            today_rows.sort(key=lambda r: (r.sort_order, r.id))
            return today_rows[0]
        scoped.sort(key=lambda r: (-int(r.sort_order or 0), r.id))
        return scoped[0]
    return _pick_from_pool(
        scoped, mode,
        fixed_id=_config_int('manna_verse_fixed_id', 0),
        seed=f'verse:{app}:{lang}:{plat}',
    )


def serialize_daily_verse(row):
    if not row:
        return None
    return {
        'id': row.id,
        'verse_text': row.verse_text,
        'reference': row.reference or '',
        'bible_book': row.bible_book or '',
        'chapter': row.chapter,
        'verse_start': row.verse_start,
        'verse_end': row.verse_end,
        'wallpaper_id': row.wallpaper_id,
        'image_url': row.image_url or '',
        'image_url_night': row.image_url_night or '',
        'active_date': row.active_date.isoformat() if row.active_date else None,
        'sort_order': row.sort_order,
    }


def serialize_prayer_from_daily_verse(row, kind):
    """祷告页文案：与首页同一条今日经文；kind 只影响晨/晚背景图。"""
    if not row:
        return None
    kind = (kind or '').strip()
    if kind not in ('morning', 'night'):
        kind = 'morning'
    title = (getattr(row, 'prayer_title', None) or '').strip()
    if kind == 'night':
        image_url = (row.image_url_night or '').strip() or (row.image_url or '').strip()
    else:
        image_url = (row.image_url or '').strip()
    return {
        'id': row.id,
        'kind': kind,
        'title': title,
        'body': getattr(row, 'prayer_body', None) or '',
        'verse_text': row.verse_text or '',
        'reference': row.reference or '',
        'image_url': image_url,
        'version': 1,
    }


# 早晚祷告窗（用户本地墙钟）：早 06:00–17:00（含 17:00）；晚 17:00:01–次日 05:59:59
_PRAYER_MORNING_START = time(6, 0, 0)
_PRAYER_MORNING_END = time(17, 0, 0)


def resolve_tz_name(raw, fallback='UTC'):
    """校验 IANA 时区名；非法则回退 fallback，再非法则 UTC。"""
    candidates = [
        str(raw or '').strip(),
        str(fallback or '').strip(),
        'UTC',
    ]
    for name in candidates:
        if not name:
            continue
        try:
            ZoneInfo(name)
            return name
        except Exception:
            continue
    return 'UTC'


def resolve_prayer_period(local_dt):
    """由用户本地 datetime 得到 (kind, business_date)。

    - morning: 06:00:00 ≤ t ≤ 17:00:00，business_date = 当天
    - night: 其余时间；00:00–05:59 的 business_date = 前一天
    """
    if not isinstance(local_dt, datetime):
        raise TypeError('local_dt must be datetime')
    local_date = local_dt.date()
    # astimezone 后的 wall-clock；忽略微秒避免边界抖动
    t = local_dt.time().replace(microsecond=0)
    if _PRAYER_MORNING_START <= t <= _PRAYER_MORNING_END:
        return 'morning', local_date
    if t > _PRAYER_MORNING_END:
        return 'night', local_date
    return 'night', local_date - timedelta(days=1)


def apply_request_timezone(user, tz_raw, *, persist=True):
    """解析请求 tz；合法且与档案不同时可回写 user.timezone。返回 IANA 名。"""
    fallback = getattr(user, 'timezone', None) or 'UTC'
    sent = str(tz_raw or '').strip()
    if sent:
        try:
            ZoneInfo(sent)
            tz_name = sent
        except Exception:
            tz_name = resolve_tz_name('', fallback)
            sent = ''
    else:
        tz_name = resolve_tz_name('', fallback)
    if persist and sent and getattr(user, 'timezone', None) != tz_name:
        try:
            user.timezone = tz_name
            user.save(update_fields=['timezone', 'updated_at'])
        except Exception:
            logger.exception('persist user.timezone failed user_id=%s', getattr(user, 'id', None))
    return tz_name


def user_local_now(user, tz_raw=None, *, persist=True):
    """用户本地当前时刻 + 选用的 IANA 名。"""
    tz_name = apply_request_timezone(user, tz_raw, persist=persist)
    local_dt = dj_tz.now().astimezone(ZoneInfo(tz_name))
    return local_dt, tz_name


def get_prayer_reminder(user, app_key, tz=None, *, persist_tz=True):
    """首页红点状态：当前时段 + 该业务日是否已祷告（顶层字段，可直接 merge 进 results）。"""
    local_dt, tz_name = user_local_now(user, tz, persist=persist_tz)
    kind, business_date = resolve_prayer_period(local_dt)
    app = _manna_app(app_key if app_key is not None else getattr(user, 'app_key', None))
    row = ActivityDay.objects.filter(
        user_id=user.id, app_key=app, date=business_date,
    ).first()
    flags = row.flags if row and isinstance(row.flags, dict) else {}
    done = bool(flags.get(kind))
    return {
        'prayer_dot': (not done),
        'prayer_done': done,
        'prayer_kind': kind,
        'prayer_business_date': business_date.isoformat(),
        'prayer_label_key': 'amen_done' if done else 'amen',
        'prayer_tz': tz_name,
    }


def pick_prayer(app_key, language, platform, kind):
    """Same-language pool for morning/night; rotate by AppConfig manna_prayer_rotate."""
    app = _manna_app(app_key)
    lang = normalize_lang(language or '')
    plat = normalize_platform(platform or '')
    kind = (kind or '').strip()
    mode = _rotate_mode('manna_prayer_rotate', 'date')
    qs = PrayerContent.objects.filter(enabled=True, app_key=app)
    if kind:
        qs = qs.filter(kind=kind)
    candidates = []
    for row in qs.order_by('sort_order', 'id'):
        if scope_score(row, lang, plat, app_key=app) >= 0:
            candidates.append(row)
    if not candidates:
        return []
    exact = [r for r in candidates if normalize_lang(r.language or '') == lang] if lang else []
    pool = exact or [r for r in candidates if not normalize_lang(r.language or '')] or candidates

    def _fixed_for(k):
        if k == 'night':
            return _config_int('manna_prayer_night_id', 0)
        return _config_int('manna_prayer_morning_id', 0)

    if kind:
        picked = _pick_from_pool(
            pool, mode,
            fixed_id=_fixed_for(kind),
            seed=f'prayer:{kind}:{app}:{lang}:{plat}',
        )
        return [picked] if picked else []
    by_kind = {}
    for row in pool:
        by_kind.setdefault(row.kind, []).append(row)
    out = []
    for k in sorted(by_kind.keys()):
        picked = _pick_from_pool(
            by_kind[k], mode,
            fixed_id=_fixed_for(k),
            seed=f'prayer:{k}:{app}:{lang}:{plat}',
        )
        if picked:
            out.append(picked)
    return out


def day_is_active(row):
    """真实功课日：祷告/灵修有时长或 Amen；读经满 60 秒；答题有次数。不含首页 Amen / 经文 Amen。"""
    if not row:
        return False
    if int(row.pray_sec or 0) > 0:
        return True
    if int(row.devotion_sec or 0) > 0:
        return True
    if int(row.read_sec or 0) >= READ_ACTIVE_SEC:
        return True
    if int(row.quiz_count or 0) > 0:
        return True
    flags = row.flags if isinstance(row.flags, dict) else {}
    return any(bool(flags.get(k)) for k in PRACTICE_FLAGS)


# 一条 rule 只认其中一个达标键；hidden / cta_path / series 是展示位
BADGE_STAT_KEYS = (
    'amen_count', 'quiz_count', 'devotion_sessions', 'story_unlocks',
    'morning_prayer', 'night_prayer', 'quiz_level',
    'bible_read_days', 'practice_days', 'practice_streak', 'return_after_gap_days',
)
BADGE_CTA_BY_KEY = {
    'amen_count': '/pages/home/home',
    'morning_prayer': '/pagesA/prayer/prayer?kind=morning',
    'night_prayer': '/pagesA/prayer/prayer?kind=night',
    'quiz_count': '/pages/quiz/quiz',
    'quiz_level': '/pages/quiz/quiz',
    'devotion_sessions': '/pages/devotion/list',
    'story_unlocks': '/pagesA/quiz/stories',
    'bible_read_days': '/pages/bible/bible',
    'practice_days': '/pagesA/calendar/calendar',
    'practice_streak': '/pagesA/calendar/calendar',
    'return_after_gap_days': '/pages/home/home',
}
_HOW_TO = {
    'en': {
        'amen_count': 'Say Amen {n} time(s)',
        'quiz_count': 'Finish {n} quiz round(s)',
        'devotion_sessions': 'Complete {n} devotion session(s)',
        'story_unlocks': 'Unlock {n} story card(s)',
        'morning_prayer': 'Complete {n} morning prayer(s)',
        'night_prayer': 'Complete {n} night prayer(s)',
        'quiz_level': 'Clear quiz level {n}',
        'bible_read_days': 'Read Scripture on {n} day(s)',
        'practice_days': 'Practice on {n} day(s)',
        'practice_streak': 'Be present {n} day(s) in a row',
        'return_after_gap_days': 'Welcome back. You are still held.',
    },
    'zh': {
        'amen_count': '完成 {n} 次 Amen',
        'quiz_count': '完成 {n} 局测验',
        'devotion_sessions': '完成 {n} 节灵修',
        'story_unlocks': '解锁 {n} 张故事卡',
        'morning_prayer': '完成 {n} 次晨祷',
        'night_prayer': '完成 {n} 次晚祷',
        'quiz_level': '通关测验第 {n} 关',
        'bible_read_days': '读经满额 {n} 天',
        'practice_days': '完成功课 {n} 天',
        'practice_streak': '连续在场 {n} 天',
        'return_after_gap_days': '欢迎回来。你仍被持守。',
    },
    'es': {
        'amen_count': 'Di Amén {n} vez(ces)',
        'quiz_count': 'Termina {n} ronda(s) de quiz',
        'devotion_sessions': 'Completa {n} sesión(es) de devocional',
        'story_unlocks': 'Desbloquea {n} historia(s)',
        'morning_prayer': 'Completa {n} oración(es) de mañana',
        'night_prayer': 'Completa {n} oración(es) de noche',
        'quiz_level': 'Supera el nivel {n} del quiz',
        'bible_read_days': 'Lee la Escritura {n} día(s)',
        'practice_days': 'Practica {n} día(s)',
        'practice_streak': 'Permanece presente {n} día(s) seguidos',
        'return_after_gap_days': 'Bienvenido de nuevo. Sigues siendo sostenido.',
    },
    'pt': {
        'amen_count': 'Diga Amém {n} vez(es)',
        'quiz_count': 'Conclua {n} rodada(s) de quiz',
        'devotion_sessions': 'Complete {n} sessão(ões) de devocional',
        'story_unlocks': 'Desbloqueie {n} história(s)',
        'morning_prayer': 'Complete {n} oração(ões) da manhã',
        'night_prayer': 'Complete {n} oração(ões) da noite',
        'quiz_level': 'Passe o nível {n} do quiz',
        'bible_read_days': 'Leia as Escrituras em {n} dia(s)',
        'practice_days': 'Pratique em {n} dia(s)',
        'practice_streak': 'Esteja presente {n} dia(s) seguidos',
        'return_after_gap_days': 'Bem-vindo de volta. Você ainda é sustentado.',
    },
    'ja': {
        'amen_count': 'Amen を {n} 回となえる',
        'quiz_count': 'クイズを {n} 回終える',
        'devotion_sessions': 'デボーションを {n} 課完了する',
        'story_unlocks': 'ストーリーを {n} 枚解除する',
        'morning_prayer': '朝の祈りを {n} 回終える',
        'night_prayer': '夜の祈りを {n} 回終える',
        'quiz_level': 'クイズレベル {n} をクリアする',
        'bible_read_days': '聖書を {n} 日読む',
        'practice_days': '課業を {n} 日行う',
        'practice_streak': '{n} 日連続して在る',
        'return_after_gap_days': 'おかえり。あなたはなお守られている。',
    },
    'ko': {
        'amen_count': 'Amen을 {n}번 하다',
        'quiz_count': '퀴즈 {n}판을 마치다',
        'devotion_sessions': '묵상 {n}과를 마치다',
        'story_unlocks': '이야기 카드 {n}장을 열다',
        'morning_prayer': '아침 기도를 {n}번 마치다',
        'night_prayer': '저녁 기도를 {n}번 마치다',
        'quiz_level': '퀴즈 {n}단계를 통과하다',
        'bible_read_days': '성경을 {n}일 읽다',
        'practice_days': '실천을 {n}일 하다',
        'practice_streak': '{n}일 연속으로 있다',
        'return_after_gap_days': '다시 환영합니다. 당신은 여전히 붙들려 있습니다.',
    },
}


def _badge_lang(lang=None):
    if lang is None:
        lang = get_config_context()[1]
    code = normalize_lang(lang or '') or 'en'
    return code if code in _HOW_TO else 'en'


def rule_stat_key(rule):
    """一条 rule 只认白名单里的第一个达标键。"""
    if not isinstance(rule, dict):
        return ''
    for key in BADGE_STAT_KEYS:
        if key in rule:
            return key
    return ''


def rule_is_hidden(rule):
    return bool(isinstance(rule, dict) and rule.get('hidden'))


def rule_how_to(rule, lang=None):
    """把徽章 rule JSON 转成可读达标说明（按请求语言）。隐藏章不展示标准。"""
    if not isinstance(rule, dict) or not rule or rule_is_hidden(rule):
        return ''
    key = rule_stat_key(rule)
    if not key:
        return ''
    n = int(rule.get(key) or 0)
    tmpl = (_HOW_TO.get(_badge_lang(lang)) or _HOW_TO['en']).get(key) or ''
    return tmpl.replace('{n}', str(n)) if tmpl else ''


def rule_cta_path(rule):
    if not isinstance(rule, dict):
        return ''
    custom = str(rule.get('cta_path') or '').strip()
    if custom:
        return custom[:255]
    return BADGE_CTA_BY_KEY.get(rule_stat_key(rule), '')


def _rule_met(rule, stats):
    if not isinstance(rule, dict) or not rule:
        return False
    key = rule_stat_key(rule)
    if not key:
        return False
    return int(stats.get(key) or 0) >= int(rule.get(key) or 0)


def _max_consecutive(dates):
    if not dates:
        return 0
    ordered = sorted(dates)
    best = cur = 1
    for i in range(1, len(ordered)):
        delta = (ordered[i] - ordered[i - 1]).days
        if delta == 1:
            cur += 1
            if cur > best:
                best = cur
        elif delta > 1:
            cur = 1
    return best


def _max_return_gap(dates):
    """连续功课日之间跳过的本地日数最大值（漏做天数）。"""
    if len(dates) < 2:
        return 0
    ordered = sorted(dates)
    best = 0
    for i in range(1, len(ordered)):
        skipped = (ordered[i] - ordered[i - 1]).days - 1
        if skipped > best:
            best = skipped
    return best


def collect_badge_stats(user, app_key=None):
    app = _manna_app(app_key)
    amen_agg = BibleAmen.objects.filter(user_id=user.id, app_key=app).aggregate(
        amen_count=Count('id'),
        morning_prayer=Count('id', filter=Q(scene='prayer:morning')),
        night_prayer=Count('id', filter=Q(scene='prayer:night')),
    )
    quiz_qs = MannaQuizAttempt.objects.filter(
        user_id=user.id, app_key=app, status=MannaQuizAttempt.STATUS_COMPLETED,
    )
    quiz_count = quiz_qs.count()
    passed_ids = set(
        quiz_qs.filter(score__gte=F('total'), total__gt=0).values_list('level_id', flat=True)
    )
    passed_ids |= set(
        quiz_qs.filter(manna_delta__gt=0).values_list('level_id', flat=True)
    )
    max_level = 0
    if passed_ids:
        max_level = int(
            MannaQuizLevel.objects.filter(id__in=passed_ids).aggregate(m=Max('level_no')).get('m') or 0
        )
    devotion_n = 0
    for ids in DevotionProgress.objects.filter(user_id=user.id, app_key=app).values_list('completed_session_ids', flat=True):
        devotion_n += len(ids or [])
    need = int(get_config('manna_bible_read_seconds', 300) or 300)
    day_rows = list(ActivityDay.objects.filter(user_id=user.id, app_key=app).order_by('date'))
    practice_dates = [r.date for r in day_rows if day_is_active(r)]
    bible_days = 0
    if need > 0:
        bible_days = sum(1 for r in day_rows if int(r.read_sec or 0) >= need)
    return {
        'amen_count': int(amen_agg.get('amen_count') or 0),
        'quiz_count': quiz_count,
        'devotion_sessions': devotion_n,
        'story_unlocks': MannaQuizStoryUnlock.objects.filter(
            user_id=user.id, app_key=app,
        ).count(),
        'morning_prayer': int(amen_agg.get('morning_prayer') or 0),
        'night_prayer': int(amen_agg.get('night_prayer') or 0),
        'quiz_level': max_level,
        'bible_read_days': bible_days,
        'practice_days': len(practice_dates),
        'practice_streak': _max_consecutive(practice_dates),
        'return_after_gap_days': _max_return_gap(practice_dates),
    }


def _scoped_badge_defs(app, lang, plat):
    rows = list(BadgeDef.objects.filter(enabled=True, app_key=app).order_by('sort_order', 'id'))
    return filter_scoped_rows(rows, lang or '', plat or '', group_key='code', app_key=app)


def serialize_badge_brief(d):
    return {
        'id': d.id,
        'code': d.code,
        'title': d.title,
        'icon_url': d.icon_url or '',
    }


def evaluate_manna_badges(user, app_key=None, stats=None, language=None, platform=None):
    """按规则补发已达标徽章；返回本次新发的简要列表。不发币、不撤销。"""
    app = _manna_app(app_key)
    ctx_app, ctx_lang, ctx_plat = get_config_context()
    lang = language if language is not None else ctx_lang
    plat = platform if platform is not None else ctx_plat
    stats = stats if stats is not None else collect_badge_stats(user, app)
    earned_ids = set(
        BadgeUser.objects.filter(user_id=user.id, app_key=app).values_list('badge_id', flat=True)
    )
    new_defs = []
    to_create = []
    for d in _scoped_badge_defs(app, lang, plat):
        if d.id in earned_ids:
            continue
        if _rule_met(d.rule or {}, stats):
            to_create.append(BadgeUser(user_id=user.id, app_key=app, badge_id=d.id))
            new_defs.append(d)
    if to_create:
        BadgeUser.objects.bulk_create(to_create, ignore_conflicts=True)
    return [serialize_badge_brief(d) for d in new_defs]


def _fold_list_visible(items):
    """同系列未解锁只露出门槛最低的下一枚。"""
    next_by_series = {}
    for it in items:
        if it.get('earned') or it.get('hidden'):
            continue
        series = it.get('series') or it.get('code') or ''
        prev = next_by_series.get(series)
        if prev is None or int(it.get('target') or 0) < int(prev.get('target') or 0):
            next_by_series[series] = it
    for it in items:
        if it.get('earned') or it.get('hidden'):
            it['list_visible'] = True
        else:
            series = it.get('series') or it.get('code') or ''
            it['list_visible'] = next_by_series.get(series) is it
    return items


def serialize_badge_item(d, stats, earned_map, lang=None):
    rule = d.rule if isinstance(d.rule, dict) else {}
    key = rule_stat_key(rule)
    hidden = rule_is_hidden(rule)
    target = int(rule.get(key) or 0) if key else 0
    progress = int(stats.get(key) or 0) if key else 0
    if target > 0 and progress > target:
        progress = target
    earned = d.id in earned_map
    how = '' if hidden and not earned else rule_how_to(rule, lang)
    cta = '' if hidden and not earned else rule_cta_path(rule)
    earned_at = earned_map.get(d.id)
    title = d.title
    icon = d.icon_url or ''
    out_rule = rule
    if hidden and not earned:
        title = ''
        icon = ''
        how = ''
        cta = ''
        progress = 0
        target = 0
        out_rule = {}
    return {
        'id': d.id,
        'code': d.code,
        'title': title,
        'icon_url': icon,
        'rule': out_rule,
        'how_to': how,
        'earned': earned,
        'earned_at': earned_at.isoformat() if earned_at else None,
        'progress': progress,
        'target': target,
        'cta_path': cta,
        'hidden': hidden,
        'series': str(rule.get('series') or key or d.code),
        'newly_earned': False,
        'sort_order': int(d.sort_order or 0),
    }


def list_manna_badges(user, app_key=None, language=None, platform=None):
    """结算后返回列表：已获（新在上）→ 系列下一枚 → 隐藏剪影。"""
    app = _manna_app(app_key)
    _ctx_app, ctx_lang, ctx_plat = get_config_context()
    lang = language if language is not None else ctx_lang
    plat = platform if platform is not None else ctx_plat
    stats = collect_badge_stats(user, app)
    evaluate_manna_badges(user, app, stats=stats, language=lang, platform=plat)
    earned_map = {
        b.badge_id: b.earned_at
        for b in BadgeUser.objects.filter(user_id=user.id, app_key=app)
    }
    items = [
        serialize_badge_item(d, stats, earned_map, lang)
        for d in _scoped_badge_defs(app, lang, plat)
    ]
    _fold_list_visible(items)
    earned = [it for it in items if it['earned']]
    earned.sort(key=lambda it: it.get('earned_at') or '', reverse=True)
    nxt = [it for it in items if (not it['earned']) and it.get('list_visible') and not it.get('hidden')]
    nxt.sort(key=lambda it: (int(it.get('sort_order') or 0), it.get('id') or 0))
    mystery = [it for it in items if (not it['earned']) and it.get('hidden')]
    mystery.sort(key=lambda it: (int(it.get('sort_order') or 0), it.get('id') or 0))
    return earned + nxt + mystery


def grant_kind_amen_reward(user, app_key, kind, config_key, reason, once_key, local_date=None):
    """每种 kind 每个本地日最多发一次奖励。"""
    ensure_manna_defaults()
    app = _manna_app(app_key)
    prev = get_config_context()
    try:
        set_config_context(language='', platform='', app_key=app)
        reward = int(get_config(config_key, 10) or 0)
    finally:
        set_config_context(language=prev[1], platform=prev[2], app_key=prev[0])
    day = local_date or dj_tz.now().date()
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day[:10])
        except ValueError:
            day = dj_tz.now().date()
    wallet = ensure_manna_wallet(user, app)
    with transaction.atomic():
        wallet = MannaWallet.objects.select_for_update().get(pk=wallet.pk)
        stats = wallet.stats if isinstance(wallet.stats, dict) else {}
        dates = stats.get(once_key) if isinstance(stats.get(once_key), dict) else {}
        kind_key = str(kind or 'prayer')[:32]
        ref_id = '%s:%s' % (kind_key, day.isoformat())
        already = dates.get(kind_key) == day.isoformat()
        if not already and MannaLedger.objects.filter(
            user_id=user.id, app_key=app, reason=reason, ref_id=ref_id,
        ).exists():
            already = True
        delta = 0
        if reward > 0 and not already:
            apply_manna_delta(
                user, app, reward, reason,
                ref_type='amen', ref_id=ref_id, remark='%s %s' % (kind_key, day),
            )
            wallet = MannaWallet.objects.select_for_update().get(pk=wallet.pk)
            stats = wallet.stats if isinstance(wallet.stats, dict) else {}
            dates = stats.get(once_key) if isinstance(stats.get(once_key), dict) else {}
            dates[kind_key] = day.isoformat()
            stats[once_key] = dates
            wallet.stats = stats
            wallet.save(update_fields=['stats', 'updated_at'])
            delta = reward
    snap = get_manna_wallet_snapshot(user, app_key=app)
    return {
        'manna_delta': delta,
        'balance': snap['balance'],
        'already': already,
    }


def grant_bible_read_reward(user, app_key, local_date=None):
    """读经/听经满 N 秒发一次 +reward，每天最多 cap 次。"""
    ensure_manna_defaults()
    app = _manna_app(app_key)
    prev = get_config_context()
    try:
        set_config_context(language='', platform='', app_key=app)
        reward = int(get_config('manna_bible_read_reward', 10) or 0)
        need = int(get_config('manna_bible_read_seconds', 300) or 300)
        cap = int(get_config('manna_bible_read_daily_cap', 2) or 2)
    finally:
        set_config_context(language=prev[1], platform=prev[2], app_key=prev[0])
    snap = get_manna_wallet_snapshot(user, app_key=app)
    day = local_date or dj_tz.now().date()
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day[:10])
        except ValueError:
            day = dj_tz.now().date()
    if reward <= 0 or need <= 0 or cap <= 0:
        return {'manna_delta': 0, 'balance': snap['balance'], 'granted': 0, 'read_sec': 0}
    wallet = ensure_manna_wallet(user, app)
    with transaction.atomic():
        MannaWallet.objects.select_for_update().get(pk=wallet.pk)
        row = ActivityDay.objects.filter(user_id=user.id, app_key=app, date=day).first()
        read_sec = int(row.read_sec or 0) if row else 0
        earned_slots = min(cap, read_sec // need)
        prefix = day.isoformat()
        already = MannaLedger.objects.filter(
            user_id=user.id, app_key=app, reason=REASON_BIBLE_READ,
            ref_id__startswith=prefix + '#',
        ).count()
        to_grant = max(0, earned_slots - already)
        delta = 0
        for i in range(to_grant):
            slot = already + i + 1
            ref_id = '%s#%s' % (prefix, slot)
            if MannaLedger.objects.filter(
                user_id=user.id, app_key=app, reason=REASON_BIBLE_READ, ref_id=ref_id,
            ).exists():
                continue
            apply_manna_delta(
                user, app, reward, REASON_BIBLE_READ,
                ref_type='bible_read', ref_id=ref_id,
                remark='read %ss slot %s' % (read_sec, slot),
            )
            delta += reward
    snap = get_manna_wallet_snapshot(user, app_key=app)
    return {
        'manna_delta': delta,
        'balance': snap['balance'],
        'granted': to_grant,
        'read_sec': read_sec,
    }
