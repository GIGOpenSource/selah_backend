#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""运营后台请求/响应序列化器。

字段用途概览：
- *SaveSerializer：运营台新建/更新；更新常配合「请求体键集合」做 partial patch。
- delete_id：同接口硬删引导题/安全词/热线/快捷开场。
- language/platform 空串=全局作用域维度。
- AppUserSave / StaffUserSave：运营改 App 用户额度或运营账号 RBAC。
- RolePermissionsSave：roles={role: [perm_keys]} 整表覆盖保存。
"""
import json

from rest_framework import serializers

_VIDEO_URL_EXTS = {'.mp4', '.m4v', '.mov', '.webm'}
_IMAGE_URL_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.bmp'}


def _url_ext(url):
    path = str(url or '').split('?', 1)[0].split('#', 1)[0]
    slash = path.rfind('/')
    dot = path.rfind('.')
    if dot < 0 or dot < slash:
        return ''
    return path[dot:].lower()


def _is_video_url(url):
    return _url_ext(url) in _VIDEO_URL_EXTS


def _is_image_url(url):
    return _url_ext(url) in _IMAGE_URL_EXTS


def _wallpaper_request_keys(raw):
    if raw is None:
        return set()
    try:
        return set(raw.keys())
    except AttributeError:
        return set()


def normalize_wallpaper_media(merged, raw):
    """按最终是视频还是图片整理壁纸媒体字段。

    视频：video_url 必填，poster_url 必须是图片且不能等于视频地址，image_url 写成封面。
    图片：image_url 必填，video_url 与 poster_url 清空。
    raw 里出现的键视为本次显式提交，用来区分「换成图片」和库里残留的视频地址。
    """
    raw_keys = _wallpaper_request_keys(raw)

    def _submitted(key):
        if key not in raw_keys:
            return None
        try:
            value = raw.get(key)
        except Exception:
            value = None
        return str(value or '').strip()

    image_url = str(merged.get('image_url') or '').strip()
    video_url = str(merged.get('video_url') or '').strip()
    poster_url = str(merged.get('poster_url') or '').strip()
    media_type = str(merged.get('media_type') or '').strip()
    submitted_type = _submitted('media_type')
    submitted_video = _submitted('video_url')
    submitted_image = _submitted('image_url')

    if submitted_type in ('static', 'video'):
        kind = submitted_type
    elif submitted_video and _is_video_url(submitted_video):
        kind = 'video'
    elif submitted_image and _is_video_url(submitted_image):
        kind = 'video'
    elif submitted_image and not _is_video_url(submitted_image):
        kind = 'static'
    elif media_type == 'video' or _is_video_url(video_url):
        kind = 'video'
    else:
        kind = 'static'

    if kind == 'video':
        if not video_url:
            if submitted_image and _is_video_url(submitted_image):
                video_url = submitted_image
            elif _is_video_url(image_url):
                video_url = image_url
        if not video_url:
            raise serializers.ValidationError({'video_url': 'required for video'})
        if not poster_url or poster_url == video_url or not _is_image_url(poster_url):
            raise serializers.ValidationError({'poster_url': 'required for video'})
        out = {
            'media_type': 'video',
            'video_url': video_url,
            'poster_url': poster_url,
            'image_url': poster_url,
        }
        if 'duration_sec' in merged and merged.get('duration_sec') not in (None, ''):
            try:
                dur = int(merged.get('duration_sec'))
            except (TypeError, ValueError):
                raise serializers.ValidationError({'duration_sec': 'max 15 seconds'})
            if dur > 15:
                raise serializers.ValidationError({'duration_sec': 'max 15 seconds'})
            out['duration_sec'] = dur
        return out

    if not image_url or _is_video_url(image_url):
        raise serializers.ValidationError({'image_url': 'required for static'})
    return {
        'media_type': 'static',
        'image_url': image_url,
        'video_url': '',
        'poster_url': '',
    }

from models.models import Quote, Topic, Wallpaper, OnboardingQuestion, AiSafetyKeyword, CrisisHotline, AiQuickPrompt, WelcomeScreen
from tools.selah_enums import (
    APP_LANGS, FAITH_TYPES, INTEREST_TAGS, SAFETY_CATEGORIES, TOPIC_CATEGORIES,
    EXPLORE_TOPIC_CATEGORIES, TOPIC_TYPE_SLUGS,
    choice_tuples, values_of, validate_membership, normalize_faith,
)


def _norm_app_key(value):
    from tools.selah_quota import normalize_app_key
    return normalize_app_key(value or '')


def _norm_scope_language(value, *, allow_blank=True):
    """作用域语言：空=全局；非空须在 APP_LANGS。"""
    from tools.selah_quota import normalize_lang
    lang = normalize_lang(value or '')
    if not lang:
        if allow_blank:
            return ''
        raise serializers.ValidationError('language required')
    allowed = set(values_of(APP_LANGS))
    if lang not in allowed:
        raise serializers.ValidationError(f'language must be one of {sorted(allowed)}')
    return lang


def _norm_scope_platform(value):
    """作用域设备：空=全部；非空仅 ios/android。"""
    from tools.selah_quota import normalize_platform
    plat = normalize_platform(value or '')
    if value and not plat:
        raise serializers.ValidationError('platform must be ios, android, or blank')
    return plat


def as_tag_name(value):
    """主主题等单值标签：兼容数组 / {name} / 空白。"""
    if value is None or value is False:
        return ''
    if isinstance(value, (list, tuple)):
        return as_tag_name(value[0] if value else '')
    if isinstance(value, dict):
        return as_tag_name(value.get('name') or value.get('value') or value.get('label') or '')
    return str(value).strip()


def as_tag_names(value):
    """视觉风格等多值标签：兼容 JSON 字符串、单值、{name}。"""
    if value is None or value is False or value == '':
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text[:1] in '[{':
            try:
                return as_tag_names(json.loads(text))
            except (TypeError, ValueError):
                pass
        return [text]
    if isinstance(value, dict):
        name = as_tag_name(value)
        return [name] if name else []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(as_tag_names(item))
        return out
    name = as_tag_name(value)
    return [name] if name else []


class ScopeFieldsMixin:
    """给 SaveSerializer 统一 app_key / language / platform 校验。"""

    def validate_app_key(self, value):
        return _norm_app_key(value)

    def validate_language(self, value):
        return _norm_scope_language(value, allow_blank=True)

    def validate_platform(self, value):
        return _norm_scope_platform(value)


class QuoteAdminSerializer(serializers.ModelSerializer):
    """语录全字段输出（运营列表）。"""
    class Meta:
        model = Quote
        fields = '__all__'


class TopicAdminSerializer(serializers.ModelSerializer):
    """主题全字段输出。"""
    class Meta:
        model = Topic
        fields = '__all__'


class WallpaperAdminSerializer(serializers.ModelSerializer):
    """壁纸全字段输出；usage_count/uses 为列表注解的使用次数（运营台表格读 uses）。"""

    class Meta:
        model = Wallpaper
        fields = '__all__'

    def to_representation(self, instance):
        data = super().to_representation(instance)
        uses = int(getattr(instance, 'usage_count', 0) or 0)
        data['usage_count'] = uses
        data['uses'] = uses
        data['theme'] = as_tag_name(getattr(instance, 'theme', '') or '')
        data['visual_tags'] = as_tag_names(getattr(instance, 'visual_tags', None))
        data['scenario_tags'] = as_tag_names(getattr(instance, 'scenario_tags', None))
        data['emotion_tags'] = as_tag_names(getattr(instance, 'emotion_tags', None))
        data['name_en'] = getattr(instance, 'name_en', '') or ''
        return data


class OnboardingAdminSerializer(serializers.ModelSerializer):
    """引导题全字段输出。"""
    class Meta:
        model = OnboardingQuestion
        fields = '__all__'


class SafetyKeywordSerializer(serializers.ModelSerializer):
    """安全关键词全字段输出。"""
    class Meta:
        model = AiSafetyKeyword
        fields = '__all__'


class HotlineSerializer(serializers.ModelSerializer):
    """危机热线全字段输出。"""
    class Meta:
        model = CrisisHotline
        fields = '__all__'


class QuickPromptSerializer(serializers.ModelSerializer):
    """快捷开场全字段输出。"""
    class Meta:
        model = AiQuickPrompt
        fields = '__all__'


class WelcomeScreenSerializer(serializers.ModelSerializer):
    """冷启欢迎页全字段输出。"""
    class Meta:
        model = WelcomeScreen
        fields = '__all__'


class AdminLoginSerializer(serializers.Serializer):
    """运营登录：username + password（bcrypt 校验）。"""
    username = serializers.CharField()
    password = serializers.CharField()


class ConfigSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """保存 AppConfig。

    Fields:
        key/value：配置键与 JSON 值。
        language/platform/app_key：作用域；空=全局行。
        priority：同维冲突时数字大优先；默认 0。
        remark：运营备注，不影响 App 读取。
    """
    key = serializers.CharField()
    value = serializers.JSONField()
    remark = serializers.CharField(required=False, allow_blank=True, default='')
    language = serializers.CharField(required=False, allow_blank=True, default='')
    platform = serializers.CharField(required=False, allow_blank=True, default='')
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)
    priority = serializers.IntegerField(required=False, default=0)


class ConfigPackDeleteSerializer(ScopeFieldsMixin, serializers.Serializer):
    """删除某精确维度下的一组配置键（维度打包列表）。

    传入 priority 时只删该优先级；未传则删该维下全部优先级（兼容旧调用）。
    """
    keys = serializers.ListField(child=serializers.CharField(), min_length=1)
    language = serializers.CharField(required=False, allow_blank=True, default='')
    platform = serializers.CharField(required=False, allow_blank=True, default='')
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)
    priority = serializers.IntegerField(required=False, allow_null=True, default=None)


class TopicSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """主题新建/更新：is_vip=会员锁定；
    slug=类型主题键（bible_verses/prayers/quotes/affirmations）；
    category=栏目分类（draw_near/health/journey，兼容历史 TOPIC_CATEGORIES）；
    app_key×language×platform 空=全部。
    """
    id = serializers.IntegerField(required=False)
    name = serializers.CharField(max_length=100)
    slug = serializers.CharField(required=False, allow_blank=True, default='', max_length=64)
    cover_url = serializers.CharField(required=False, allow_blank=True, default='')
    description = serializers.CharField(required=False, allow_blank=True, default='')
    is_vip = serializers.BooleanField(required=False, default=False)
    category = serializers.CharField(required=False, allow_blank=True, default='', max_length=64)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    sort_weight = serializers.IntegerField(required=False, default=0)

    def validate_category(self, value):
        raw = (value or '').strip()
        if not raw:
            return ''
        allowed = set(values_of(EXPLORE_TOPIC_CATEGORIES)) | set(values_of(TOPIC_CATEGORIES))
        aliases = {
            'faith': 'faith',
            'draw_near': 'draw_near',
            'health': 'health',
            'journey': 'journey',
        }
        key = raw.lower()
        if key in allowed or raw in allowed:
            return key if key in allowed else raw
        if key in aliases:
            return aliases[key]
        # 运营台栏目分类与类型 slug 都允许落库，避免编辑已有行 400
        type_slugs = set(values_of(TOPIC_TYPE_SLUGS))
        if key in type_slugs:
            return key
        return raw


class WallpaperSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """壁纸新建/更新：is_vip=付费壁纸；tags 为字符串列表；
    app_key×language×platform 空=全部。3.0：media_type / video 字段。
    更新（带 id）且请求里没有图片/视频字段时，不校验媒体，便于只改 status。
    带了媒体字段时，由 wallpaper_save 先和库里的行合并再校验。
    """
    id = serializers.IntegerField(required=False)
    image_url = serializers.CharField(required=False, allow_blank=True, default='')
    media_type = serializers.ChoiceField(choices=['static', 'video'], required=False, default='static')
    video_url = serializers.CharField(required=False, allow_blank=True, default='')
    poster_url = serializers.CharField(required=False, allow_blank=True, default='')
    duration_sec = serializers.IntegerField(required=False, allow_null=True)
    is_vip = serializers.BooleanField(required=False, default=False)
    tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    name_en = serializers.CharField(required=False, allow_blank=True, default='', max_length=120)
    name_es = serializers.CharField(required=False, allow_blank=True, default='', max_length=120)
    name_pt = serializers.CharField(required=False, allow_blank=True, default='', max_length=120)
    theme = serializers.CharField(required=False, allow_blank=True, default='', max_length=80)
    visual_tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    scenario_tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    emotion_tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    taxonomy = serializers.JSONField(required=False, default=dict)
    display_tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    sort_weight = serializers.IntegerField(required=False, default=0)
    status = serializers.IntegerField(required=False)

    def validate_status(self, value):
        if value not in (0, 1):
            raise serializers.ValidationError('status must be 0 or 1')
        return value

    def to_internal_value(self, data):
        """单选主题可能传来数组；多选标签可能是 JSON 字符串。"""
        if hasattr(data, 'copy'):
            data = data.copy()
        else:
            try:
                data = dict(data)
            except (TypeError, ValueError):
                data = {}
        if 'theme' in data:
            data['theme'] = as_tag_name(data.get('theme'))
        for key in ('visual_tags', 'scenario_tags', 'emotion_tags', 'display_tags', 'tags'):
            if key in data:
                data[key] = as_tag_names(data.get(key))
        return super().to_internal_value(data)

    def validate(self, attrs):
        # 带 id 的更新在视图里和已有行合并后再校验。这里只拦新建。
        if attrs.get('id'):
            return attrs
        initial = getattr(self, 'initial_data', None)
        raw = {}
        if initial is not None:
            try:
                raw = {key: initial.get(key) for key in initial.keys()}
            except AttributeError:
                raw = {}
        merged = {
            'image_url': attrs.get('image_url') or '',
            'media_type': attrs.get('media_type') or '',
            'video_url': attrs.get('video_url') or '',
            'poster_url': attrs.get('poster_url') or '',
        }
        if 'duration_sec' in attrs:
            merged['duration_sec'] = attrs.get('duration_sec')
        attrs.update(normalize_wallpaper_media(merged, raw))
        return attrs


class QuizSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    id = serializers.IntegerField(required=False)
    title = serializers.CharField(max_length=128)
    subtitle = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    cover_url = serializers.CharField(required=False, allow_blank=True, default='')
    sort_order = serializers.IntegerField(required=False, default=0)
    enabled = serializers.BooleanField(required=False, default=True)
    is_vip = serializers.BooleanField(required=False, default=False)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)


class QuizQuestionSaveSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    quiz_id = serializers.IntegerField(min_value=1)
    stem = serializers.CharField(max_length=512)
    options = serializers.JSONField()
    correct_key = serializers.CharField(max_length=32)
    explain = serializers.CharField(required=False, allow_blank=True, default='', max_length=512)
    sort_order = serializers.IntegerField(required=False, default=0)
    enabled = serializers.BooleanField(required=False, default=True)


class AppIconSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    id = serializers.IntegerField(required=False)
    icon_key = serializers.CharField(max_length=64)
    title = serializers.CharField(max_length=64)
    preview_url = serializers.CharField(max_length=512)
    is_vip = serializers.BooleanField(required=False, default=False)
    sort_order = serializers.IntegerField(required=False, default=0)
    enabled = serializers.BooleanField(required=False, default=True)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)


class WidgetCardSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """小组件卡片新建/更新：文字 + 描述 + 图片 + 图片类型。"""
    id = serializers.IntegerField(required=False)
    text = serializers.CharField(max_length=256)
    description = serializers.CharField(required=False, allow_blank=True, default='', max_length=512)
    image_url = serializers.CharField(max_length=512)
    image_type = serializers.ChoiceField(
        choices=('square', 'wide'), required=False, default='square',
    )
    sort_order = serializers.IntegerField(required=False, default=0)
    enabled = serializers.BooleanField(required=False, default=True)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)


class QuoteSaveSerializer(serializers.Serializer):
    """语录新建/更新（含标签校验）。

    Fields:
        countries: 空=全球；非空为国家二字码列表。
        in_cold_start_pool: 是否进冷启运营池。
        app_key/platform: 空=全部 App/设备；language 为正文语言（必填白名单，不可空）。
        更新时 content 可不传（仅改开关）；新建必须有 content。
    """
    id = serializers.IntegerField(required=False)
    content = serializers.CharField(required=False, allow_blank=False)
    language = serializers.CharField(required=False, default='en')
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    faith_tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    interest_tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    content_type = serializers.CharField(required=False, allow_blank=True, default='', max_length=80)
    theme = serializers.CharField(required=False, allow_blank=True, default='', max_length=80)
    scenario_tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    emotion_tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    taxonomy = serializers.JSONField(required=False, default=dict)
    display_tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    content_es = serializers.CharField(required=False, allow_blank=True, default='')
    content_pt = serializers.CharField(required=False, allow_blank=True, default='')
    is_vip = serializers.BooleanField(required=False, default=False)
    countries = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    audio_url = serializers.CharField(required=False, allow_blank=True, default='')
    source_ref = serializers.CharField(required=False, allow_blank=True, default='', max_length=120)
    in_cold_start_pool = serializers.BooleanField(required=False, default=False)
    sort_weight = serializers.IntegerField(required=False, default=0)
    topic_id = serializers.IntegerField(required=False, allow_null=True)
    status = serializers.IntegerField(required=False, default=1)

    def validate_status(self, value):
        if value not in (0, 1):
            raise serializers.ValidationError('status must be 0 or 1')
        return value

    def to_internal_value(self, data):
        """兼容历史字段名 in_ops_pool → in_cold_start_pool；归一化标签字段。"""
        if hasattr(data, 'copy'):
            data = data.copy()
        else:
            try:
                data = dict(data)
            except (TypeError, ValueError):
                data = {}
        if 'in_ops_pool' in data and 'in_cold_start_pool' not in data:
            data['in_cold_start_pool'] = data.get('in_ops_pool')
        if 'content_type' in data:
            data['content_type'] = as_tag_name(data.get('content_type'))
        if 'theme' in data:
            data['theme'] = as_tag_name(data.get('theme'))
        for key in ('scenario_tags', 'emotion_tags', 'display_tags'):
            if key in data:
                data[key] = as_tag_names(data.get(key))
        return super().to_internal_value(data)

    def validate_language(self, value):
        """正文语言必填，且在 APP_LANGS（与作用域空维不同）。"""
        return _norm_scope_language(value or 'en', allow_blank=False)

    def validate_app_key(self, value):
        return _norm_app_key(value)

    def validate_platform(self, value):
        return _norm_scope_platform(value)

    def validate_interest_tags(self, value):
        """兴趣标签须属于 INTEREST_TAGS。"""
        ok, bad = validate_membership(value or [], INTEREST_TAGS)
        if bad:
            raise serializers.ValidationError(f'unknown interest_tags: {bad}')
        return ok

    def validate_faith_tags(self, value):
        """信仰标签归一化后须属于 FAITH_TYPES。"""
        normalized = [normalize_faith(v) for v in (value or [])]
        ok, bad = validate_membership(normalized, FAITH_TYPES, field_name='faith_tags')
        if bad:
            raise serializers.ValidationError(f'unknown faith_tags: {bad}')
        return ok

    def validate(self, attrs):
        # 新建必须有正文；更新可只改开关
        if not attrs.get('id') and not attrs.get('content'):
            raise serializers.ValidationError('content required for create')
        return attrs


class IdSerializer(serializers.Serializer):
    """仅含资源 id（软删/切换状态等）。"""
    id = serializers.IntegerField()


class BatchIdSerializer(serializers.Serializer):
    """支持单 id 或 ids 数组，用于批量删除等。"""
    id = serializers.IntegerField(required=False)
    ids = serializers.ListField(child=serializers.IntegerField(), required=False)

    def validate(self, attrs):
        if not attrs.get('id') and not attrs.get('ids'):
            raise serializers.ValidationError('请提供 id 或 ids')
        return attrs

    def get_ids(self):
        """统一返回 list[int]，兼容单 id 和 ids 数组。"""
        if self.validated_data.get('ids'):
            return list(self.validated_data['ids'])
        if self.validated_data.get('id'):
            return [self.validated_data['id']]
        return []


class OnboardingSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """引导题保存/删除。

    Fields:
        delete_id: 有则硬删并忽略其它字段。
        id: 有则 patch；无 id 且无 delete_id 则新建（需 question_key+title）。
        language/platform/app_key: 空=全局维度。
    """
    id = serializers.IntegerField(required=False)
    delete_id = serializers.IntegerField(required=False)
    question_key = serializers.CharField(required=False, allow_blank=True, max_length=64)
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    options = serializers.JSONField(required=False, default=list)
    sort_order = serializers.IntegerField(required=False, default=0)
    enabled = serializers.BooleanField(required=False, default=True)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)

    def validate(self, attrs):
        if attrs.get('delete_id'):
            return attrs
        if attrs.get('id'):
            return attrs
        if not attrs.get('question_key') or not attrs.get('title'):
            raise serializers.ValidationError('question_key and title required')
        from tools.selah_enums import all_onboarding_key_values
        allowed = set(all_onboarding_key_values())
        if attrs.get('question_key') and attrs['question_key'] not in allowed:
            raise serializers.ValidationError(
                f'question_key must be one of {sorted(allowed)}'
            )
        return attrs


class SafetyKeywordSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """安全词保存/删除：category 须在 SAFETY_CATEGORIES；新建需 category+keyword。"""
    id = serializers.IntegerField(required=False)
    delete_id = serializers.IntegerField(required=False)
    category = serializers.CharField(required=False, allow_blank=True, max_length=32)
    keyword = serializers.CharField(required=False, allow_blank=True, max_length=128)
    enabled = serializers.BooleanField(required=False, default=True)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)

    def validate_category(self, value):
        if not value:
            return value
        allowed = set(values_of(SAFETY_CATEGORIES))
        if value not in allowed:
            raise serializers.ValidationError(f'category must be one of {sorted(allowed)}')
        return value

    def validate(self, attrs):
        if attrs.get('delete_id') or attrs.get('id'):
            return attrs
        if not attrs.get('category') or not attrs.get('keyword'):
            raise serializers.ValidationError('category and keyword required')
        return attrs


class HotlineSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """危机热线保存/删除：新建需 label+phone_or_url；country 可 GLOBAL。"""
    id = serializers.IntegerField(required=False)
    delete_id = serializers.IntegerField(required=False)
    country = serializers.CharField(required=False, allow_blank=True, max_length=8)
    label = serializers.CharField(required=False, allow_blank=True, max_length=128)
    phone_or_url = serializers.CharField(required=False, allow_blank=True, max_length=128)
    enabled = serializers.BooleanField(required=False, default=True)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)

    def validate(self, attrs):
        if attrs.get('delete_id') or attrs.get('id'):
            return attrs
        if not attrs.get('label') or not attrs.get('phone_or_url'):
            raise serializers.ValidationError('label and phone_or_url required')
        return attrs


class QuickPromptSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """快捷开场保存/删除：新建需 title+prompt。"""
    id = serializers.IntegerField(required=False)
    delete_id = serializers.IntegerField(required=False)
    title = serializers.CharField(required=False, allow_blank=True, max_length=100)
    prompt = serializers.CharField(required=False, allow_blank=True)
    sort_order = serializers.IntegerField(required=False, default=0)
    enabled = serializers.BooleanField(required=False, default=True)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)

    def validate(self, attrs):
        if attrs.get('delete_id'):
            return attrs
        if attrs.get('id'):
            return attrs
        if not attrs.get('title') or not attrs.get('prompt'):
            raise serializers.ValidationError('title and prompt required')
        return attrs


class WelcomeScreenSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """欢迎页保存/删除：新建需 title；version 上调可强制客户端再看。"""
    id = serializers.IntegerField(required=False)
    delete_id = serializers.IntegerField(required=False)
    title = serializers.CharField(required=False, allow_blank=True, max_length=128)
    subtitle = serializers.CharField(required=False, allow_blank=True, max_length=255)
    cta_text = serializers.CharField(required=False, allow_blank=True, max_length=64)
    image_url = serializers.CharField(required=False, allow_blank=True, max_length=512)
    version = serializers.IntegerField(required=False, default=1, min_value=1)
    priority = serializers.IntegerField(required=False, default=0)
    sort_order = serializers.IntegerField(required=False, default=0)
    enabled = serializers.BooleanField(required=False, default=True)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)

    def validate(self, attrs):
        if attrs.get('delete_id'):
            return attrs
        if attrs.get('id'):
            return attrs
        if not attrs.get('title'):
            raise serializers.ValidationError('title required')
        return attrs


class InterstitialScreenSaveSerializer(ScopeFieldsMixin, serializers.Serializer):
    """教育夹层保存/删除：新建需 kind+title；kind 须在 INTERSTITIAL_KINDS。"""
    id = serializers.IntegerField(required=False)
    delete_id = serializers.IntegerField(required=False)
    kind = serializers.CharField(required=False, allow_blank=True, max_length=32)
    title = serializers.CharField(required=False, allow_blank=True, max_length=128)
    subtitle = serializers.CharField(required=False, allow_blank=True, max_length=255)
    cta_text = serializers.CharField(required=False, allow_blank=True, max_length=64)
    image_url = serializers.CharField(required=False, allow_blank=True, max_length=512)
    version = serializers.IntegerField(required=False, default=1, min_value=1)
    priority = serializers.IntegerField(required=False, default=0)
    sort_order = serializers.IntegerField(required=False, default=0)
    enabled = serializers.BooleanField(required=False, default=True)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)

    def validate_kind(self, value):
        if not value:
            return value
        from tools.selah_enums import interstitial_kind_values
        allowed = set(interstitial_kind_values())
        if value not in allowed:
            raise serializers.ValidationError(f'kind must be one of {sorted(allowed)}')
        return value

    def validate(self, attrs):
        if attrs.get('delete_id'):
            return attrs
        if attrs.get('id'):
            return attrs
        if not attrs.get('kind') or not attrs.get('title'):
            raise serializers.ValidationError('kind and title required')
        return attrs


class AppUserSaveSerializer(serializers.Serializer):
    """App 用户运营侧更新。

    Fields:
        free_quota / vip_daily_remaining：人工调额。
        vip_level / vip_expire_at / is_trial：人工开通或改期。
        status：0 禁用 / 1 正常。不可改 role。
    """
    id = serializers.IntegerField()
    status = serializers.IntegerField(required=False)
    display_name = serializers.CharField(required=False, allow_blank=True, max_length=64)
    vip_level = serializers.ChoiceField(required=False, choices=['free', 'vip', 'plus_vip'])
    vip_expire_at = serializers.DateTimeField(required=False, allow_null=True)
    is_trial = serializers.BooleanField(required=False)
    free_quota = serializers.IntegerField(required=False, min_value=0)
    vip_daily_remaining = serializers.IntegerField(required=False, min_value=0)
    marketing_opt_in = serializers.BooleanField(required=False)


class StaffUserSaveSerializer(serializers.Serializer):
    """运营账号新建/更新。

    Fields:
        role: operator|admin|super_admin（超管分配有额外护栏）。
        password: 明文入参，模型 save 时哈希；新建必填。
        app_keys: 可管理 App 多选（如 ['selah','manna']）；空列表=全部 App。
    """
    id = serializers.IntegerField(required=False)
    username = serializers.CharField(required=False, max_length=64)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    password = serializers.CharField(required=False, allow_blank=True, min_length=6)
    role = serializers.ChoiceField(required=False, choices=['operator', 'admin', 'super_admin'])
    status = serializers.IntegerField(required=False)
    app_keys = serializers.ListField(
        child=serializers.CharField(max_length=32),
        required=False,
        allow_empty=True,
    )

    def validate(self, attrs):
        if not attrs.get('id') and not attrs.get('username'):
            raise serializers.ValidationError('username required for create')
        if not attrs.get('id') and not attrs.get('password'):
            raise serializers.ValidationError('password required for create')
        if 'app_keys' in attrs:
            raw = attrs.get('app_keys') or []
            allowed = {'selah', 'manna'}
            cleaned = []
            for item in raw:
                k = str(item or '').strip().lower()
                if k in allowed and k not in cleaned:
                    cleaned.append(k)
            attrs['app_keys'] = cleaned
        return attrs


class RolePermissionsSaveSerializer(serializers.Serializer):
    """角色→权限列表映射保存：roles={role_name: [perm_key, ...]}。"""
    roles = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))


class MembershipProductSaveSerializer(serializers.Serializer):
    """会员商品新建/更新。

    Fields:
        app_key: 品牌维；新建必填非空。
        store_product_id: 商店侧 ID，与 platform 联合唯一。
        trial_days: 覆盖全局试用天数；null 表示用全局。
        duration_days: 正式开通天数。
    """
    id = serializers.IntegerField(required=False)
    app_key = serializers.CharField(required=False, allow_blank=True, max_length=32)
    plan_type = serializers.ChoiceField(required=False, choices=['monthly', 'yearly'])
    platform = serializers.ChoiceField(required=False, choices=['ios', 'android'])
    store_product_id = serializers.CharField(required=False, max_length=128)
    title = serializers.CharField(required=False, allow_blank=True, max_length=128)
    price_display = serializers.CharField(required=False, allow_blank=True, max_length=64)
    price_display_i18n = serializers.DictField(required=False, child=serializers.CharField(allow_blank=True, max_length=64))
    price_cents = serializers.IntegerField(required=False, min_value=0)
    currency = serializers.CharField(required=False, allow_blank=True, max_length=8)
    duration_days = serializers.IntegerField(required=False, min_value=1)
    trial_days = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    enabled = serializers.BooleanField(required=False)
    sort_weight = serializers.IntegerField(required=False)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        if not attrs.get('id'):
            if not attrs.get('plan_type') or not attrs.get('platform') or not attrs.get('store_product_id'):
                raise serializers.ValidationError('plan_type, platform, store_product_id required')
            if not (attrs.get('app_key') or '').strip():
                raise serializers.ValidationError('app_key required')
        return attrs


class OrderStatusSerializer(serializers.Serializer):
    """订单状态变更：refunded/revoked 可能触发用户 VIP 降级。"""
    id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=['active', 'expired', 'refunded', 'revoked'])


class ReportStatusSerializer(serializers.Serializer):
    """举报处理：soft_delete_quote=True 时同步软删语录。"""
    id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=['pending', 'handled', 'dismissed'])
    soft_delete_quote = serializers.BooleanField(required=False, default=False)


class AffirmationTemplateSaveSerializer(serializers.Serializer):
    """运营肯定语模板新建/更新/删除。"""
    id = serializers.IntegerField(required=False)
    delete_id = serializers.IntegerField(required=False)
    content = serializers.CharField(required=False, allow_blank=False, max_length=160)
    sort_weight = serializers.IntegerField(required=False, default=0)
    status = serializers.IntegerField(required=False, default=1)
    app_key = serializers.CharField(required=False, allow_blank=True, default='', max_length=32)
    language = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)
    platform = serializers.CharField(required=False, allow_blank=True, default='', max_length=16)

    def validate(self, attrs):
        if attrs.get('delete_id'):
            return attrs
        if not attrs.get('id') and not attrs.get('content'):
            raise serializers.ValidationError('content required')
        return attrs
