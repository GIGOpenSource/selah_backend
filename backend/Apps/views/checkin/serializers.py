#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""打卡偏好序列化器。

Fields:
    checkin_record_enabled: 关闭后首开不自动打、无弹层；关闭时服务端强制关提醒。
    checkin_remind_enabled: 本地提醒；开启前必须已开记录。
"""
from rest_framework import serializers

from tools.selah_quota import REMINDER_TIME_KEYS


class CheckinPrefsSerializer(serializers.Serializer):
    """打卡记录/提醒开关（均可 partial）。"""
    checkin_record_enabled = serializers.BooleanField(
        required=False, help_text='是否记录打卡（关闭后首开不自动打、无弹层）'
    )
    checkin_remind_enabled = serializers.BooleanField(
        required=False, help_text='本地提醒开关（依赖 record 开启）'
    )


class ReminderPrefsSerializer(serializers.Serializer):
    """2.0 提醒中心三开关（均可 partial）。"""
    record_enabled = serializers.BooleanField(required=False)
    streak_remind_enabled = serializers.BooleanField(required=False)
    checkin_remind_enabled = serializers.BooleanField(required=False)


class ReminderTimeSerializer(serializers.Serializer):
    """Reminders 点「完成」：改某一条的本地墙钟。"""
    key = serializers.ChoiceField(
        choices=[(k, k) for k in REMINDER_TIME_KEYS],
        help_text='writing / practice / quotes / checkin / streak',
    )
    hour = serializers.IntegerField(required=False, min_value=0, max_value=23)
    minute = serializers.IntegerField(required=False, min_value=0, max_value=59)
    time = serializers.CharField(
        required=False, allow_blank=True, max_length=8,
        help_text='HH:mm；与 hour+minute 二选一',
    )

    def validate(self, attrs):
        hour, minute = attrs.get('hour'), attrs.get('minute')
        if hour is not None and minute is not None:
            return attrs
        raw = str(attrs.get('time') or '').strip()
        if ':' in raw:
            hh, mm = raw.split(':', 1)
            mm = mm.split(':', 1)[0]
            try:
                attrs['hour'] = max(0, min(23, int(hh)))
                attrs['minute'] = max(0, min(59, int(mm)))
            except (TypeError, ValueError):
                raise serializers.ValidationError({'time': 'invalid HH:mm'})
            return attrs
        raise serializers.ValidationError('pass hour+minute or time (HH:mm)')
