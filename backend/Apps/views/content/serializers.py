#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""App 内容域序列化器与语录卡片组装。

字段用途概览：
- ContentIdSerializer：曝光/不喜欢/取消收藏等仅需语录 ID。
- LikeSerializer：liked 控制写入或删除 UserLike，并驱动新人喜欢任务。
- CollectSerializer：folder_id 空则落到默认 Favorites 夹。
- ReportSerializer：reason 枚举与运营台对齐；detail 可选补充。
- quote_card：统一 Feed/详情卡片，locked=VIP 且用户无有效会员。
"""
from rest_framework import serializers

from models.models import Quote, Topic, Wallpaper, Affirmation
from tools.selah_enums import REPORT_REASONS, choice_tuples


class QuoteSerializer(serializers.ModelSerializer):
    """语录模型全字段序列化（运营/调试）；App Feed 多用 quote_card。"""
    class Meta:
        model = Quote
        fields = '__all__'


class TopicSerializer(serializers.ModelSerializer):
    """主题全字段；列表接口会额外挂 followed / locked。"""
    class Meta:
        model = Topic
        fields = '__all__'


class WallpaperSerializer(serializers.ModelSerializer):
    """壁纸全字段；列表接口额外挂 locked。"""
    class Meta:
        model = Wallpaper
        fields = '__all__'


class ContentIdSerializer(serializers.Serializer):
    """仅 content_id：曝光、不喜欢、取消收藏等单内容操作。"""
    content_id = serializers.IntegerField(min_value=1, help_text='内容 ID')
    target_type = serializers.ChoiceField(
        choices=['quote', 'affirmation'],
        required=False,
        default='quote',
        help_text='quote=语录，affirmation=自我肯定；默认 quote',
    )


class LikeSerializer(serializers.Serializer):
    """喜欢/取消喜欢。

    Fields:
        content_id: 内容 ID（语录或肯定语，由 target_type 决定）。
        target_type: quote|affirmation，默认 quote。
        liked: true=写入 UserLike（幂等）；false=删除行（进度 current 会降，完成标记不回退）。
    """
    content_id = serializers.IntegerField(min_value=1, help_text='内容 ID')
    target_type = serializers.ChoiceField(
        choices=['quote', 'affirmation'],
        required=False,
        default='quote',
        help_text='quote=语录，affirmation=自我肯定；默认 quote',
    )
    liked = serializers.BooleanField(required=False, default=True, help_text='true=喜欢，false=取消')

class DislikeSerializer(serializers.Serializer):
    """不喜欢/取消屏蔽。

    Fields:
        content_id: 语录 ID。
        disliked: true=写入 UserDislike；false=删除屏蔽（取消屏蔽）。
    """
    content_id = serializers.IntegerField(min_value=1, help_text='语录 ID')
    disliked = serializers.BooleanField(required=False, default=True, help_text='true=屏蔽，false=取消屏蔽')

class CollectSerializer(serializers.Serializer):
    """收藏入参。

    Fields:
        content_id: 内容 ID（语录或肯定语，由 target_type 决定）。
        target_type: quote|affirmation，默认 quote。
        folder_id: 目标夹；空则服务端取/建默认 Favorites。同一内容再收藏会换夹。
    """
    content_id = serializers.IntegerField(min_value=1, help_text='内容 ID')
    target_type = serializers.ChoiceField(
        choices=['quote', 'affirmation'],
        required=False,
        default='quote',
        help_text='quote=语录，affirmation=自我肯定；默认 quote',
    )
    folder_id = serializers.IntegerField(required=False, allow_null=True, help_text='收藏夹 ID，空=默认夹')


class ReportSerializer(serializers.Serializer):
    """举报入参。

    Fields:
        content_id / reason / detail：新建 ContentReport，status 默认 pending，不自动下架。
    """
    content_id = serializers.IntegerField(min_value=1, help_text='语录 ID')
    reason = serializers.ChoiceField(
        choices=choice_tuples(REPORT_REASONS),
        required=False, default='other',
        help_text='inappropriate|hate|spam|copyright|other',
    )
    detail = serializers.CharField(required=False, allow_blank=True, default='', max_length=500, help_text='补充说明')


class FollowTopicSerializer(serializers.Serializer):
    """关注主题：follow=true 写入 TopicFollow；false 删除。"""
    topic_id = serializers.IntegerField(min_value=1, help_text='主题 ID')
    follow = serializers.BooleanField(required=False, default=True, help_text='true=关注')

class FollowFolderSerializer(serializers.Serializer):
    """关注收藏夹：follow=true 写入 FolderFollow；false 删除。仅限本人夹。"""
    folder_id = serializers.IntegerField(min_value=1, help_text='收藏夹 ID')
    follow = serializers.BooleanField(required=False, default=True, help_text='true=关注')


class FollowAllAffirmationsSerializer(serializers.Serializer):
    """一键关注/取消本人全部自我肯定。"""
    follow = serializers.BooleanField(required=False, default=True, help_text='true=全部关注；false=全部取消')


class WallpaperIdSerializer(serializers.Serializer):
    """设置壁纸：付费壁纸需 VIP，否则 403 + pay_trigger_source。"""
    wallpaper_id = serializers.IntegerField(min_value=1, help_text='壁纸 ID')


class FolderSaveSerializer(serializers.Serializer):
    """收藏夹新建/改名：有 id 则改名（限本人）；无 id 则新建非默认夹。"""
    id = serializers.IntegerField(required=False, allow_null=True, help_text='有则更新')
    name = serializers.CharField(max_length=64, help_text='夹名')


class IdSerializer(serializers.Serializer):
    """通用资源 id（删夹、删自我肯定等）。"""
    id = serializers.IntegerField(min_value=1, help_text='资源 ID')


class AffirmationSaveSerializer(serializers.Serializer):
    """自我肯定保存：有 id 更新；无 id 新建；长度 1～160；3.0 可选主题/壁纸/meta。"""
    id = serializers.IntegerField(required=False, allow_null=True, help_text='有则更新')
    content = serializers.CharField(min_length=1, max_length=160, help_text='肯定语文案 1-160')
    theme_id = serializers.IntegerField(required=False, allow_null=True)
    wallpaper_id = serializers.IntegerField(required=False, allow_null=True)
    meta = serializers.JSONField(required=False)


class WidgetPrefsSerializer(serializers.Serializer):
    """小组件偏好；缺键由服务端 normalize。"""
    theme_key = serializers.CharField(required=False, allow_blank=True, max_length=64)
    border_key = serializers.CharField(required=False, allow_blank=True, max_length=64)
    refresh = serializers.ChoiceField(
        required=False, choices=['hourly', 'daily', 'manual'],
    )
    quote_source = serializers.ChoiceField(
        required=False, choices=['recommend', 'followed'],
    )
    followed_topic_ids = serializers.ListField(
        required=False, child=serializers.IntegerField(min_value=1),
    )
    show_buttons = serializers.BooleanField(required=False)
    customize = serializers.BooleanField(required=False)


class AlarmPrefsSerializer(serializers.Serializer):
    """祷告闹钟偏好。"""
    enabled = serializers.BooleanField(required=False)
    hour = serializers.IntegerField(required=False, min_value=0, max_value=23)
    minute = serializers.IntegerField(required=False, min_value=0, max_value=59)


class PrayerLockPrefsSerializer(serializers.Serializer):
    """祷告锁偏好：enabled + 所选功能 id 列表 + 今日已祷告日期。"""
    enabled = serializers.BooleanField(required=False)
    app_ids = serializers.ListField(
        required=False, child=serializers.CharField(max_length=64),
    )
    last_prayer_date = serializers.CharField(
        required=False, allow_blank=True, max_length=32,
    )


class SelectAppIconSerializer(serializers.Serializer):
    icon_key = serializers.CharField(max_length=64)


class QuizStartSerializer(serializers.Serializer):
    quiz_id = serializers.IntegerField(min_value=1)


class QuizAnswerSerializer(serializers.Serializer):
    attempt_id = serializers.IntegerField(min_value=1)
    question_id = serializers.IntegerField(min_value=1)
    option_key = serializers.CharField(max_length=32)


def quote_card(q: Quote, user, liked_ids=None, collected_ids=None):
    """组装 Feed/详情用语录卡片。

    Args:
        liked_ids: 可选预计算喜欢集合，避免 N+1。
        collected_ids: 可选预计算收藏集合，避免 N+1。
    """
    locked = bool(q.is_vip) and not user.is_vip_active
    liked_ids = liked_ids or set()
    collected_ids = collected_ids or set()
    return {
        'kind': 'quote',
        'id': q.id,
        'content': q.content,
        'language': q.language,
        'source_ref': getattr(q, 'source_ref', None) or '',
        'faith_tags': q.faith_tags or [],
        'interest_tags': q.interest_tags or [],
        'is_vip': q.is_vip,
        'locked': locked,
        'liked': q.id in liked_ids,
        'collected': q.id in collected_ids,
        'audio_url': q.audio_url or '',
        'in_cold_start_pool': q.in_cold_start_pool,
        'topic_id': q.topic_id,
    }


def affirmation_card(a: Affirmation, liked_ids=None, collected_ids=None):
    """组装 Feed 用自我肯定卡片（仅本人可见；与语录 id 空间独立，须看 kind）。"""
    liked_ids = liked_ids or set()
    collected_ids = collected_ids or set()
    return {
        'kind': 'affirmation',
        'id': a.id,
        'content': a.content,
        'language': '',
        'source_ref': '',
        'faith_tags': [],
        'interest_tags': [],
        'is_vip': False,
        'locked': False,
        'liked': a.id in liked_ids,
        'collected': a.id in collected_ids,
        'audio_url': '',
        'in_cold_start_pool': False,
        'topic_id': None,
        'theme_id': getattr(a, 'theme_id', None),
        'wallpaper_id': getattr(a, 'wallpaper_id', None),
        'meta': getattr(a, 'meta', None) or {},
    }