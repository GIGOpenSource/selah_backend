#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""P3 每日打卡：首开即打、周条三态、爱心冻结、断签清零、领次挂同日。

核心业务规则：
- 以用户本地自然日（timezone）判定「今天」；连续天数只计 STATUS_CHECKED，冻结日不加 streak。
- 漏打日数 ≤ streak_miss_reset_days 且爱心够 → 把漏打日写成 frozen 并扣爱心（revive）；
  否则清零 streak 再从今天 +1（restart）。
- Free 用户打卡发 checkin_reward 次额度（受 free_quota_cap 限制）；VIP 不发免费次。
- 每 streak_heart_refill_every 天补发爱心，封顶 streak_heart_max。
"""
import logging
import random
from datetime import timedelta

from django.db import transaction
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import CheckinRecord, User
from Apps.views.checkin.serializers import (
    CheckinPrefsSerializer, ReminderPrefsSerializer, ReminderTimeSerializer,
)
from tools.permissions import IsTokenValid
from tools.selah_quota import (
    get_config, user_local_today, add_free_quota, expire_vip_if_needed, user_public_dict,
    normalize_reminder_prefs, apply_reminder_prefs,
)
from tools.utils import ApiResponse

logger = logging.getLogger(__name__)

# 复活成功弹层文案池；subtitle 可含 {n}=复活后的 streak 天数
REVIVE_COPY = [
    {
        'title': 'You revived your streak!',
        'subtitle': '{n} day streak success',
        'cta': 'Continue tomorrow',
        'cta_key': 'continue',
    },
    {
        'title': 'You revived your streak!',
        'subtitle': 'Keep going tomorrow to protect your streak',
        'cta': 'Continue tomorrow',
        'cta_key': 'continue',
    },
    {
        'title': 'You revived your streak!',
        'subtitle': 'Stay consistent — you are close to a 7-day streak',
        'cta': 'I challenge 7 days',
        'cta_key': 'challenge7',
    },
]


@extend_schema(tags=[_('Selah签到')])
class CheckinViewSet(ViewSet):
    """每日打卡：状态查询、首开打卡（含爱心冻结）、偏好开关。"""

    permission_classes = [IsTokenValid]

    def _week_view(self, user):
        """近 7 日周条三态：checked / frozen / empty。

        Returns:
            长度固定 7 的列表，从「今天-6」到「今天」；含 is_today / is_future 供 UI 灰显。

        业务说明:
            empty = 库中无记录或非 checked/frozen；未来日（理论上窗口内不会出现 >today，
            但保留 is_future 字段兼容扩展）。
        """
        today = user_local_today(user)
        # 近 7 日窗口（含今天，向前 6 天 + 今天，未来空位填到 7）
        start = today - timedelta(days=6)
        rows = {
            r.local_date: r.status
            for r in CheckinRecord.objects.filter(
                user_id=user.id, local_date__gte=start, local_date__lte=today + timedelta(days=1)
            )
        }
        days = []
        for i in range(7):
            d = start + timedelta(days=i)
            st = rows.get(d)
            if st == CheckinRecord.STATUS_CHECKED:
                state = 'checked'
            elif st == CheckinRecord.STATUS_FROZEN:
                state = 'frozen'
            else:
                state = 'empty'
            days.append({
                'date': d.isoformat(),
                'state': state,
                'checked': state == 'checked',
                'frozen': state == 'frozen',
                'is_today': d == today,
                'is_future': d > today,
            })
        return days

    def _gap_days(self, user, today):
        """上次实际打卡到昨天之间的漏打日列表。

        业务说明:
            从 last_checkin_date+1 遍历到 today 之前（不含今天）。
            无 last_checkin_date 视为无断签（首打用户）。
            注意：冻结日也会更新 last_checkin_date 吗？不会——仅 do_checkin 成功写 today，
            故 gap 基于「上次真实打勾日」。
        """
        if not user.last_checkin_date:
            return []
        gaps = []
        d = user.last_checkin_date + timedelta(days=1)
        while d < today:
            gaps.append(d)
            d += timedelta(days=1)
        return gaps

    @extend_schema(summary=_('签到状态'))
    @action(detail=False, methods=['get'], url_path='status')
    def status(self, request):
        """查询今日是否已打、连续天数、周条与弹窗开关。

        业务说明:
            先 expire_vip_if_needed，保证 vip_level / show_quota_reward 与真实到期一致。
            auto_popup = 全局配置 AND 用户开启了打卡记录；VIP 不展示额度奖励文案。
        """
        user = expire_vip_if_needed(request.user)
        today = user_local_today(user)
        already = CheckinRecord.objects.filter(
            user_id=user.id, local_date=today, status=CheckinRecord.STATUS_CHECKED
        ).exists()
        return ApiResponse(data={
            'already': already,
            'streak_days': user.checkin_streak,
            'streak_hearts': user.streak_hearts,
            'week': self._week_view(user),
            'vip_level': 'vip' if user.is_vip_active else 'free',
            # VIP 走日额度，打卡不再发 free_quota
            'show_quota_reward': not user.is_vip_active,
            'reward': int(get_config('checkin_reward', 1)),
            'auto_popup': bool(get_config('checkin_auto_popup', True)) and user.checkin_record_enabled,
            'checkin_record_enabled': user.checkin_record_enabled,
            'checkin_remind_enabled': user.checkin_remind_enabled,
            'miss_reset_days': int(get_config('streak_miss_reset_days', 3)),
            'can_checkin_today': not already,
        })

    @extend_schema(summary=_('首开打卡 / 执行打卡'))
    @action(detail=False, methods=['post'], url_path='do')
    def do_checkin(self, request):
        """进入主界面调用：当日首次成功打卡 + 可选爱心冻结漏打日。

        Returns:
            scenario: normal | revive | restart — 决定弹层文案。
            frozen_days: 本次用爱心补写的漏打日 ISO 列表。
            granted_quota / hit_cap: Free 发次结果；VIP 为 0/False。
            hearts_granted: 里程碑补发爱心数（可能为 0）。

        业务说明:
            1. 用户关闭打卡记录 → skipped，不写库、不发次。
            2. 今日已 checked → already，幂等返回。
            3. 有 gap：超阈值或爱心不足 → streak 清零再 +1；够爱心则写 frozen 行并扣心。
            4. 仅 Free 发 checkin_reward；然后 streak+1、写今日 checked。
        """
        user = expire_vip_if_needed(request.user)
        today = user_local_today(user)

        # 偏好关闭：首开不自动打、不弹层
        if not user.checkin_record_enabled:
            return ApiResponse(data={
                'already': False,
                'skipped': True,
                'streak_days': user.checkin_streak,
                'granted_quota': 0,
                'hit_cap': False,
                'week': self._week_view(user),
                'popup': None,
                'checkin_record_enabled': False,
            }, message=_('打卡记录已关闭'))

        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user.pk)
            user = expire_vip_if_needed(user)
            existing = (
                CheckinRecord.objects.select_for_update()
                .filter(user_id=user.id, local_date=today)
                .first()
            )
            # 今日已成功打勾：幂等，不再发次/加 streak
            if existing and existing.status == CheckinRecord.STATUS_CHECKED:
                return ApiResponse(data={
                    'already': True,
                    'streak_days': user.checkin_streak,
                    'granted_quota': 0,
                    'hit_cap': False,
                    'week': self._week_view(user),
                    'popup': None,
                }, message=_('今日已打卡'))

            gaps = self._gap_days(user, today)
            reset_days = int(get_config('streak_miss_reset_days', 3))
            frozen_days = []
            scenario = 'normal'
            popup = {
                'title': 'Daily check-in',
                'subtitle': f'{user.checkin_streak + 1} day streak' if user.checkin_streak else 'Start your streak',
                'cta': 'Continue tomorrow',
                'cta_key': 'continue',
            }

            if gaps:
                if len(gaps) > reset_days:
                    user.checkin_streak = 0
                    scenario = 'restart'
                    popup = {
                        'title': 'A new streak begins',
                        'subtitle': f'Your previous streak was reset after missing more than {reset_days} days',
                        'cta': 'Continue tomorrow',
                        'cta_key': 'continue',
                        'reason': 'miss_days',
                    }
                else:
                    need = len(gaps)
                    if user.streak_hearts >= need:
                        for gd in gaps:
                            CheckinRecord.objects.update_or_create(
                                user_id=user.id, local_date=gd,
                                defaults={
                                    'status': CheckinRecord.STATUS_FROZEN,
                                    'granted_quota': 0,
                                    'hit_cap': False,
                                    'vip_level': user.vip_level if user.is_vip_active else 'free',
                                },
                            )
                            frozen_days.append(gd.isoformat())
                        user.streak_hearts -= need
                        scenario = 'revive'
                        copy = random.choice(REVIVE_COPY)
                        n = user.checkin_streak + 1
                        popup = {
                            'title': copy['title'],
                            'subtitle': copy['subtitle'].format(n=n),
                            'cta': copy['cta'],
                            'cta_key': copy['cta_key'],
                        }
                    else:
                        user.checkin_streak = 0
                        scenario = 'restart'
                        popup = {
                            'title': 'A new streak begins',
                            'subtitle': 'Not enough hearts to freeze missed days',
                            'cta': 'Continue tomorrow',
                            'cta_key': 'continue',
                            'reason': 'no_hearts',
                        }

            granted = 0
            hit_cap = False
            vip_level = user.vip_level if user.is_vip_active else 'free'
            if not user.is_vip_active:
                reward = int(get_config('checkin_reward', 1))
                granted, hit_cap, user = add_free_quota(user, reward)

            user.checkin_streak = (user.checkin_streak or 0) + 1
            user.last_checkin_date = today

            hearts_granted = 0
            every_n = int(get_config('streak_heart_refill_every', 7))
            amount = int(get_config('streak_heart_refill_amount', 1))
            heart_max = int(get_config('streak_heart_max', 5))
            if every_n > 0 and user.checkin_streak % every_n == 0:
                before = user.streak_hearts or 0
                user.streak_hearts = min(heart_max, before + amount)
                hearts_granted = user.streak_hearts - before

            user.save(update_fields=[
                'checkin_streak', 'last_checkin_date', 'streak_hearts', 'updated_at',
            ])

            CheckinRecord.objects.update_or_create(
                user_id=user.id, local_date=today,
                defaults={
                    'status': CheckinRecord.STATUS_CHECKED,
                    'granted_quota': granted,
                    'hit_cap': hit_cap,
                    'vip_level': vip_level,
                },
            )

        logger.info(
            'checkin user=%s streak=%s granted=%s frozen=%s hearts+=%s scenario=%s',
            user.id, user.checkin_streak, granted, frozen_days, hearts_granted, scenario,
        )
        show_popup = user.checkin_record_enabled and bool(get_config('checkin_auto_popup', True))
        return ApiResponse(data={
            'already': False,
            'streak_days': user.checkin_streak,
            'granted_quota': granted,
            'hit_cap': hit_cap,
            'vip_level': vip_level,
            'frozen_days': frozen_days,
            'streak_hearts': user.streak_hearts,
            'hearts_granted': hearts_granted,
            'scenario': scenario,
            'miss_reset_days': reset_days,
            'week': self._week_view(user),
            'popup': popup if show_popup else None,
            'user': user_public_dict(user),
        }, message=_('打卡成功'))

    @extend_schema(summary=_('打卡设置'))
    @action(detail=False, methods=['post'], url_path='prefs')
    def prefs(self, request):
        """更新打卡记录/提醒开关（提醒依赖记录开启）。

        业务说明:
            关闭记录时强制关提醒（提醒无意义）。
            单独开提醒但记录已关 → 400，要求先开记录。
        """
        ser = CheckinPrefsSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        user = request.user
        data = ser.validated_data
        if 'checkin_record_enabled' in data:
            enabled = bool(data['checkin_record_enabled'])
            user.checkin_record_enabled = enabled
            if not enabled:
                # 记录关 → 提醒必须关，避免本地推送孤儿开关
                user.checkin_remind_enabled = False
        if 'checkin_remind_enabled' in data:
            remind = bool(data['checkin_remind_enabled'])
            if remind and not user.checkin_record_enabled:
                return ApiResponse(message=_('请先开启打卡记录'), code=400)
            user.checkin_remind_enabled = remind
        # 同步 reminder_prefs JSON，避免 1.0 prefs 与 2.0 中心脱节
        prefs = normalize_reminder_prefs(user, {
            'record_enabled': user.checkin_record_enabled,
            'checkin_remind_enabled': user.checkin_remind_enabled,
        })
        fields = apply_reminder_prefs(user, prefs)
        user.save(update_fields=fields)
        return ApiResponse(data={
            'checkin_record_enabled': user.checkin_record_enabled,
            'checkin_remind_enabled': user.checkin_remind_enabled,
            'reminder_prefs': prefs,
        })

    @extend_schema(summary=_('提醒中心偏好'))
    @action(detail=False, methods=['get', 'post'], url_path='reminder-prefs')
    def reminder_prefs(self, request):
        """2.0 提醒中心：record / streak / checkin 三开关。

        业务说明:
            关 record 强制关 streak 与 checkin；开提醒但 record 关 → 400。
            写回 User.reminder_prefs，并同步 checkin_record/remind 列。
            GET 含 times；拨开关不改钟点。
        """
        user = request.user
        if request.method == 'GET':
            prefs = normalize_reminder_prefs(user)
            return ApiResponse(data=prefs)
        ser = ReminderPrefsSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        incoming = dict(ser.validated_data)
        current = normalize_reminder_prefs(user)
        merged = {**current}
        for k, v in incoming.items():
            if k in ('record_enabled', 'streak_remind_enabled', 'checkin_remind_enabled'):
                merged[k] = bool(v)
        if not merged.get('record_enabled', True):
            merged['streak_remind_enabled'] = False
            merged['checkin_remind_enabled'] = False
        elif (
            (merged.get('streak_remind_enabled') or merged.get('checkin_remind_enabled'))
            and not merged.get('record_enabled')
        ):
            return ApiResponse(message=_('请先开启打卡记录'), code=400)
        prefs = normalize_reminder_prefs(user, merged)
        fields = apply_reminder_prefs(user, prefs)
        user.save(update_fields=fields)
        return ApiResponse(data=prefs, message=_('已保存'))

    @extend_schema(summary=_('提醒钟点'))
    @action(detail=False, methods=['get', 'post'], url_path='reminder-times')
    def reminder_times(self, request):
        """Reminders 时间选择器：读全部钟点 / 写单条。

        业务说明:
            存 User.reminder_prefs.times，与开关解耦；拨 reminder-prefs 不会清时刻。
            POST 一次改一条（点「完成」）；key 见 ReminderTimeSerializer。
        """
        user = request.user
        if request.method == 'GET':
            prefs = normalize_reminder_prefs(user)
            return ApiResponse(data=prefs.get('times') or {})
        ser = ReminderTimeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        prefs = normalize_reminder_prefs(user, {
            'times': {
                data['key']: {'hour': data['hour'], 'minute': data['minute']},
            },
        })
        fields = apply_reminder_prefs(user, prefs)
        user.save(update_fields=fields)
        return ApiResponse(data=prefs.get('times') or {}, message=_('已保存'))
