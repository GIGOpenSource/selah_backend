#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""认证域序列化器。

字段用途：
- DeviceLogin：device_id 为设备账号主键；timezone/system_lang/platform 驱动本地日与配置作用域。
- OAuthLogin：按 oauth_id 找/建独立三方账号；与设备账号不合并。
- OAuthBind：已废弃（接口仍接收字段，view 固定 410）。
- UpdateProfile：资料与音效/营销等偏好；timezone/wallpaper_id 可由 view 直写兼容。
"""
from rest_framework import serializers


class DeviceLoginSerializer(serializers.Serializer):
    """设备快速登录/注册。

    Fields:
        device_id: 客户端自建 UUID，唯一对应用户。
        timezone: IANA 时区，决定打卡/AI 本地日。
        register_country: 注册国二字码（热线匹配）。
        system_lang / platform / app_key: 配置与内容作用域。
    """
    device_id = serializers.CharField(max_length=64, help_text='客户端自建 UUID')
    timezone = serializers.CharField(required=False, default='UTC', help_text='IANA 时区')
    register_country = serializers.CharField(
        required=False, allow_blank=True, default='', help_text='注册国家二字码'
    )
    system_lang = serializers.CharField(required=False, default='en', help_text='App 语言')
    platform = serializers.ChoiceField(
        required=False, allow_blank=True, choices=['ios', 'android', ''], help_text='ios|android'
    )
    app_key = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=32, help_text='应用标识，如 selah'
    )


class OAuthBindSerializer(serializers.Serializer):
    """已废弃：保留字段以免旧客户端校验失败；服务端固定返回 410。"""
    provider = serializers.ChoiceField(choices=['google', 'apple'], help_text='google|apple')
    oauth_id = serializers.CharField(max_length=128, help_text='三方稳定 ID')
    id_token = serializers.CharField(
        required=False, allow_blank=True, default='', help_text='已废弃'
    )
    confirm_switch = serializers.BooleanField(required=False, default=False)
    email = serializers.EmailField(required=False, allow_blank=True)
    display_name = serializers.CharField(required=False, allow_blank=True)


class OAuthLoginSerializer(serializers.Serializer):
    """Google/Apple 登录（独立三方账号，与设备账号不合并）。

    Fields:
        provider: google|apple。
        oauth_id: Google 必填（openId）；Apple 可选（缺省时用 id_token 的 sub）。
        id_token: Apple 生产必填；Google 忽略。
        device_id: 前端约定必传，仅日志。
        其余：时区/语言/平台/app_key。
    """
    provider = serializers.ChoiceField(choices=['google', 'apple'], help_text='google|apple')
    oauth_id = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
        max_length=128,
        help_text='Google 必填；Apple 可选（可由 id_token.sub 补全）',
    )
    id_token = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
        help_text='Apple 生产必填；Google 忽略',
    )
    device_id = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=64, help_text='前端约定必传，仅日志'
    )
    email = serializers.EmailField(required=False, allow_blank=True, help_text='可选邮箱')
    display_name = serializers.CharField(required=False, allow_blank=True, help_text='展示名')
    timezone = serializers.CharField(required=False, default='UTC', help_text='IANA 时区')
    register_country = serializers.CharField(
        required=False, allow_blank=True, default='', help_text='注册国家二字码'
    )
    system_lang = serializers.CharField(required=False, default='en', help_text='App 语言')
    platform = serializers.ChoiceField(
        required=False, allow_blank=True, choices=['ios', 'android', ''], help_text='ios|android'
    )
    app_key = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=32, help_text='应用标识，如 selah'
    )

    def validate(self, attrs):
        provider = (attrs.get('provider') or '').strip().lower()
        oauth_id = (attrs.get('oauth_id') or '').strip()
        if provider == 'google' and not oauth_id:
            raise serializers.ValidationError({'oauth_id': 'Google login requires oauth_id'})
        attrs['oauth_id'] = oauth_id
        return attrs


class UpdateProfileSerializer(serializers.Serializer):
    """更新资料/设置（均为可选 partial）。

    Fields:
        display_name / gender / age_range / system_lang: 画像与语言。
        sound_enabled / muted / marketing_opt_in: 客户端偏好开关。
        emotion_status / denomination_pref: 写入 profile_answers（非 User 列）。
        profile_answers: 可选 partial 合并进扩展问卷答案。
    """
    display_name = serializers.CharField(required=False, allow_blank=True, max_length=64)
    gender = serializers.CharField(required=False, allow_blank=True, max_length=16)
    age_range = serializers.CharField(required=False, allow_blank=True, max_length=16)
    system_lang = serializers.CharField(required=False, allow_blank=True, max_length=16)
    sound_enabled = serializers.BooleanField(required=False)
    muted = serializers.BooleanField(required=False)
    marketing_opt_in = serializers.BooleanField(required=False)
    emotion_status = serializers.CharField(required=False, allow_blank=True, max_length=32)
    denomination_pref = serializers.CharField(required=False, allow_blank=True, max_length=32)
    profile_answers = serializers.DictField(required=False)
