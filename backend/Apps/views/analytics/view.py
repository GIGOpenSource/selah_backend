#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""App 埋点双写入库（事件名与 Firebase §9.4 一致）。

业务要点：
- 批量最多 100 条；非法项静默跳过，accepted 为实际上库条数。
- 平台归一 ios/android；时间戳支持秒/毫秒/ISO。
- 成功写入后 touch_user_active，驱动 DAU。
"""
import logging
from datetime import datetime, timezone as dt_tz

from django.utils import timezone as dj_tz
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import AnalyticsEvent, User
from tools.permissions import IsTokenValid
from tools.selah_quota import normalize_app_key, set_config_context_from_request
from tools.utils import ApiResponse

logger = logging.getLogger(__name__)

ALLOWED_PLATFORMS = {'ios', 'android'}


def normalize_platform(value):
    """归一化为 ios/android；无法识别则返回空串。

    业务说明:
        iphone/ipad → ios；其它非白名单返回 ''（不写入脏平台维度）。
    """
    p = (value or '').strip().lower()
    if p in ('iphone', 'ipad'):
        p = 'ios'
    return p if p in ALLOWED_PLATFORMS else ''


def touch_user_active(user: User, platform: str = ''):
    """刷新 last_active_at；同日已活跃则仅在 platform 变更时更新。

    业务说明:
        DAU 按 last_active_at 的 UTC 日；同日再次调用不重复写时间戳，减少写放大。
        platform 变更仍会落库，保证设备维度统计准确。
    """
    now = dj_tz.now()
    fields = []
    plat = normalize_platform(platform)
    if plat and user.platform != plat:
        user.platform = plat
        fields.append('platform')
    need_active = True
    if user.last_active_at:
        # 同 UTC 自然日已记过活跃 → 跳过时间戳更新
        if user.last_active_at.date() == now.date():
            need_active = False
    if need_active:
        user.last_active_at = now
        fields.append('last_active_at')
    if fields:
        fields.append('updated_at')
        user.save(update_fields=fields)

def record_user_event(user: User, event_name: str, params=None, *, app_key=''):
    """服务端写一条埋点（供注册/引导完成/内容打开等，不依赖客户端 batch）。"""
    now = dj_tz.now()

    # 1. 对齐batch接口：事件名校验，过滤脏数据
    event_name = (event_name or '').strip()
    if not event_name or len(event_name) > 64:
        logger.warning("record_user_event 非法事件名，已丢弃 event_name=%r user_id=%s", event_name, getattr(user, 'id', None))
        return

    app = normalize_app_key(
        app_key
        or (params or {}).get('app_key')
        or getattr(user, 'app_key', '')
        or ''
    )
    try:
        AnalyticsEvent.objects.create(
            user_id=user.id,
            app_key=app,
            event_name=event_name,
            # 2. P0修复：统一UTC日期口径，避免时区拆分统计数据
            event_date=now.astimezone(dt_tz.utc).date(),
            platform=getattr(user, 'platform', '') or '',
            system_lang=getattr(user, 'system_lang', '') or '',
            register_country=getattr(user, 'register_country', '') or '',
            params=params or {},
        )
        # 3. 对齐batch逻辑：触发用户活跃统计，更新DAU
        touch_user_active(user, getattr(user, 'platform', ''))
    except Exception:
        # 4. 异常降级：埋点失败不阻塞主业务，仅打印告警堆栈
        logger.warning(
            "record_user_event 埋点数据库写入失败 user_id=%s event=%s",
            user.id, event_name, exc_info=True
        )


def record_content_open(
    user,
    content_type,
    content_id,
    *,
    title='',
    source='server',
    app_key='',
    **extra,
):
    """统一写入 content_open，供免费产品兴趣筛选。

    params:
      content_type / content_id 必填；title / source 可选；其余进 extra。
    """
    ctype = str(content_type or '').strip()[:32]
    cid = str(content_id if content_id is not None else '').strip()[:128]
    if not ctype or not cid or user is None or not getattr(user, 'id', None):
        return
    params = {
        'content_type': ctype,
        'content_id': cid,
        'source': str(source or 'server')[:16],
    }
    if title:
        params['title'] = str(title)[:200]
    for key, value in (extra or {}).items():
        if value is None or key in params:
            continue
        params[key] = value
    record_user_event(user, 'content_open', params, app_key=app_key)

@extend_schema(tags=[_('Selah埋点')])
class AnalyticsViewSet(ViewSet):
    """埋点入库：批量事件与用户活跃刷新。"""

    permission_classes = [IsTokenValid]

    @extend_schema(summary=_('批量上报埋点（与 Firebase 同名）'))
    @action(detail=False, methods=['post'], url_path='batch')
    def batch(self, request):
        """批量写入埋点（单次最多 100 条），并刷新 last_active_at。

        业务说明:
            - events 必须为 list；超 100 截断。
            - 非 dict / 空名 / 名过长 → 跳过。
            - params 可为嵌套 dict；顶层除 name/event_name/params/ts 外扁平字段并入 params。
            - ts：>1e12 视为毫秒，否则秒；字符串按 ISO（Z→+00:00）；解析失败用 now。
            - event_date 取 UTC 日，供看板按日聚合。
        """
        raw = request.data.get('events') or []
        if not isinstance(raw, list):
            return ApiResponse(message='events must be list', code=400)
        # 硬顶 100，防刷库
        if len(raw) > 100:
            raw = raw[:100]

        user = request.user
        now = dj_tz.now()
        set_config_context_from_request(request, prefer_user=True)
        headers = getattr(request, 'headers', None) or {}
        default_app = normalize_app_key(
            headers.get('X-App-Key')
            or headers.get('app-key')
            or getattr(user, 'app_key', '')
            or ''
        )
        rows = []
        last_plat = ''
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = (item.get('name') or item.get('event_name') or '').strip()
            if not name or len(name) > 64:
                continue
            params = item.get('params') if isinstance(item.get('params'), dict) else {}
            # 扁平兼容：除 name/ts 外并入 params
            extra = {k: v for k, v in item.items() if k not in ('name', 'event_name', 'params', 'ts')}
            if extra:
                params = {**params, **extra}

            plat = normalize_platform(params.get('platform') or item.get('platform') or user.platform)
            last_plat = plat or last_plat
            lang = (params.get('system_lang') or user.system_lang or '')[:16]
            country = (params.get('register_country') or user.register_country or '')[:8]
            app = normalize_app_key(
                params.get('app_key') or item.get('app_key') or default_app
            )

            event_dt = now
            ts = item.get('ts')
            if ts is not None:
                try:
                    if isinstance(ts, (int, float)):
                        # ms or s：>1e12 按毫秒（JS Date.now）
                        sec = ts / 1000.0 if ts > 1e12 else float(ts)
                        event_dt = datetime.fromtimestamp(sec, tz=dt_tz.utc)
                    elif isinstance(ts, str) and ts:
                        event_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        if dj_tz.is_naive(event_dt):
                            event_dt = dj_tz.make_aware(event_dt, dt_tz.utc)
                except (ValueError, OSError, TypeError):
                    event_dt = now

            rows.append(AnalyticsEvent(
                user_id=user.id,
                app_key=app,
                event_name=name,
                event_date=event_dt.astimezone(dt_tz.utc).date() if event_dt.tzinfo else event_dt.date(),
                platform=plat,
                system_lang=lang,
                register_country=country,
                params=params,
            ))

        if rows:
            try:
                AnalyticsEvent.objects.bulk_create(rows, batch_size=100)
                # 用批次最后识别到的平台刷新活跃
                touch_user_active(user, last_plat or user.platform)
                logger.info('analytics_batch user=%s n=%s', user.id, len(rows))
            except Exception:
                # 异常降级：埋点失败不阻塞主业务，仅打印告警堆栈
                logger.warning(
                    "analytics_batch 埋点数据库写入失败 user=%s n=%s",
                    user.id, len(rows), exc_info=True
                )

        return ApiResponse(data={'accepted': len(rows)})
