#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah 1.0 数据模型 — 全部表名以 t_ 开头。

领域概览：
- 用户：User / AppConfig（设备登录、OAuth、VIP 额度、引导与偏好）
- 内容：Quote / Topic / Wallpaper / 喜欢收藏曝光 / Affirmation / ContentReport
- 冷启：OnboardingQuestion / WelcomeScreen（全屏欢迎文案与背景）
- 打卡：CheckinRecord（连续天数、冻结爱心）
- AI：AiConversation / AiMessage / 安全词与危机热线 / 快捷开场
- 会员：MembershipProduct / IapReceipt
- 埋点：AnalyticsEvent（与 Firebase 事件名对齐）
"""
from django.db import models
from django.utils.translation import gettext_lazy as _
from tools.password_hasher import hash_password


class User(models.Model):
    """用户表：App 设备/OAuth 用户 + 运营管理员。

    额度账本要点：
    - free_quota：非会员 AI 余额（打卡/新人奖励累加，受 free_quota_cap 限制）。
    - vip_daily_remaining：会员当日剩余次数，按本地日与 vip_daily_reset_date 刷新。
    - 二者互斥消费：有效 VIP 优先扣日额度，否则扣 free_quota。
    - streak_hearts：冻结漏打日用，一天消耗 1 颗；countries 空=全球见 Quote。
    """

    VIP_FREE = 'free'
    VIP_VIP = 'vip'
    VIP_PLUS = 'plus_vip'  # 预留，1.0 不售
    VIP_CHOICES = [
        (VIP_FREE, 'Free'),
        (VIP_VIP, 'VIP'),
        (VIP_PLUS, 'Plus VIP'),
    ]

    # ----- 账号基础（运营密码登录用 username/password；App 多用 device/oauth）-----
    username = models.CharField(max_length=64, unique=True, null=True, blank=True, verbose_name=_("用户名"))
    email = models.EmailField(null=True, blank=True, verbose_name=_("邮箱"))  # 唯一见 Meta：(email, app_key)
    password = models.CharField(max_length=256, blank=True, default='', verbose_name=_("密码"))
    role = models.CharField(max_length=20, default="user", verbose_name=_("角色"))  # user|operator|admin|super_admin
    status = models.IntegerField(default=1, verbose_name=_("状态"))  # 1 正常 0 禁用（运营禁用；与注销无关）

    # ----- 注销：冷静期 → 到期软删（is_deleted）；身份键释放后同人可新建账号 -----
    deletion_requested_at = models.DateTimeField(null=True, blank=True, verbose_name=_("申请注销UTC"))
    deletion_scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name=_("计划软删UTC"))
    is_deleted = models.BooleanField(default=False, db_index=True, verbose_name=_("已软删"))
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name=_("软删时间UTC"))

    # ----- 登录身份：google/apple 与 app_key 联合唯一（同三方号可分别绑 Selah / Manna）-----
    device_id = models.CharField(max_length=64, null=True, blank=True, db_index=True, verbose_name=_("设备ID"))
    google_id = models.CharField(max_length=128, null=True, blank=True, db_index=True, verbose_name=_("Google ID"))
    apple_id = models.CharField(max_length=128, null=True, blank=True, db_index=True, verbose_name=_("Apple ID"))
    login_type = models.CharField(max_length=20, default='device', verbose_name=_("登录方式"))

    # ----- 会员：is_vip_active 综合 vip_level + vip_expire_at -----
    vip_level = models.CharField(max_length=20, default=VIP_FREE, choices=VIP_CHOICES, verbose_name=_("会员等级"))
    vip_expire_at = models.DateTimeField(null=True, blank=True, verbose_name=_("VIP到期时间UTC"))
    is_trial = models.BooleanField(default=False, verbose_name=_("是否试用期"))

    # ----- AI 额度账本（Free 余额 vs VIP 日额度）-----
    free_quota = models.IntegerField(default=0, verbose_name=_("Free AI余额"))
    vip_daily_remaining = models.IntegerField(default=0, verbose_name=_("VIP当日剩余"))
    vip_daily_reset_date = models.DateField(null=True, blank=True, verbose_name=_("VIP日额度刷新日(本地)"))
    newcomer_bonus_granted = models.BooleanField(default=False, verbose_name=_("新人+3已发放"))  # 幂等，防重复发

    # ----- 冷启画像：completed 与 skipped 由 API 互斥维护 -----
    onboarding_completed = models.BooleanField(default=False, verbose_name=_("已完成引导"))
    onboarding_skipped = models.BooleanField(default=False, verbose_name=_("已跳过引导"))
    faith_type = models.CharField(max_length=32, blank=True, default='', verbose_name=_("信仰类型"))
    prayer_frequency = models.CharField(max_length=32, blank=True, default='', verbose_name=_("祷告频率"))
    interest_tags = models.JSONField(default=list, blank=True, verbose_name=_("兴趣标签"))  # 兼容列表；命中以 user_tags 为准
    # 用户侧标签：问卷兴趣/信仰 + 点赞兴趣；最多 30，FIFO（见 tools.user_tags）
    user_tags = models.JSONField(default=list, blank=True, verbose_name=_("用户侧标签"))
    display_name = models.CharField(max_length=64, blank=True, default='', verbose_name=_("称呼"))
    gender = models.CharField(max_length=16, blank=True, default='', verbose_name=_("性别"))
    age_range = models.CharField(max_length=16, blank=True, default='', verbose_name=_("年龄段"))
    # ----- 本地化 / 活跃（timezone 决定打卡与 AI「本地日」）-----
    timezone = models.CharField(max_length=64, default='UTC', verbose_name=_("本地时区"))
    register_country = models.CharField(max_length=8, blank=True, default='', verbose_name=_("注册国家"))  # 危机热线匹配
    system_lang = models.CharField(max_length=16, blank=True, default='en', verbose_name=_("系统语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))  # ios|android
    # 白牌 / 多 App：空=未绑定，读配置时回退到全局 app_key=''
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    # 运营账号可管理的 App 列表（多选）；空列表=全部 App
    app_keys = models.JSONField(default=list, blank=True, verbose_name=_("可管理App列表"))
    last_active_at = models.DateTimeField(null=True, blank=True, verbose_name=_("最近活跃UTC"))  # DAU
    # ----- App 偏好 -----
    bind_prompt_dismissed = models.BooleanField(default=False, verbose_name=_("关闭绑定弱提示"))
    marketing_opt_in = models.BooleanField(default=True, verbose_name=_("Marketing开关"))
    sound_enabled = models.BooleanField(default=True, verbose_name=_("音效"))
    muted = models.BooleanField(default=False, verbose_name=_("静音"))
    wallpaper_id = models.IntegerField(null=True, blank=True, verbose_name=_("当前壁纸ID"))
    # ----- 打卡：streak 只计 checked；streak_hearts 冻结漏打日 -----
    checkin_streak = models.IntegerField(default=0, verbose_name=_("连续打卡天数(实际打勾)"))
    last_checkin_date = models.DateField(null=True, blank=True, verbose_name=_("上次打卡本地日"))
    streak_hearts = models.IntegerField(default=3, verbose_name=_("冻结爱心数"))  # 一天漏打消耗 1
    checkin_record_enabled = models.BooleanField(default=True, verbose_name=_("打卡记录开关"))
    checkin_remind_enabled = models.BooleanField(default=True, verbose_name=_("打卡提醒开关"))  # 依赖 record 开启
    newcomer_like_completed = models.BooleanField(default=False, verbose_name=_("新人喜欢任务完成"))
    # ----- Selah 2.0：加长问卷答案 / 提醒中心偏好 -----
    profile_answers = models.JSONField(default=dict, blank=True, verbose_name=_("扩展问卷答案"))
    reminder_prefs = models.JSONField(default=dict, blank=True, verbose_name=_("提醒偏好"))
    # ----- Selah 3.0：小组件偏好 / Alternate Icon -----
    widget_prefs = models.JSONField(default=dict, blank=True, verbose_name=_("小组件偏好"))
    selected_icon_key = models.CharField(max_length=64, blank=True, default='', verbose_name=_("当前App图标key"))
    # ----- Selah 3.0：闹钟 / 祷告锁偏好 -----
    alarm_prefs = models.JSONField(default=dict, blank=True, verbose_name=_("祷告闹钟偏好"))
    prayer_lock_prefs = models.JSONField(default=dict, blank=True, verbose_name=_("祷告锁偏好"))

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    def save(self, *args, **kwargs):
        """保存前对明文密码做 bcrypt 哈希（已哈希则跳过）。"""
        if self.password and not self.password.startswith('$2b$'):
            self.password = hash_password(self.password[:72])
        super().save(*args, **kwargs)

    @property
    def is_authenticated(self):
        """DRF/Django 约定：已解析到本模型即视为已登录（限流、IsAuthenticated 会读）。"""
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_vip_active(self):
        """VIP/Plus 且未过期（expire_at 为空视为仍有效）。"""
        from django.utils import timezone as dj_tz
        if self.vip_level not in (self.VIP_VIP, self.VIP_PLUS):
            return False
        if self.vip_expire_at and self.vip_expire_at < dj_tz.now():
            return False
        return True

    class Meta:
        db_table = 't_user'
        verbose_name = _("用户")
        verbose_name_plural = _("用户")
        constraints = [
            # 仅未软删行参与唯一，便于注销后同 device/oauth/email 再建新用户
            models.UniqueConstraint(
                fields=['device_id', 'app_key'],
                name='t_user_device_id_app_key_uidx',
                condition=(
                    models.Q(is_deleted=False)
                    & models.Q(device_id__isnull=False)
                    & ~models.Q(device_id='')
                ),
            ),
            models.UniqueConstraint(
                fields=['google_id', 'app_key'],
                name='t_user_google_id_app_key_uidx',
                condition=(
                    models.Q(is_deleted=False)
                    & models.Q(google_id__isnull=False)
                    & ~models.Q(google_id='')
                ),
            ),
            models.UniqueConstraint(
                fields=['apple_id', 'app_key'],
                name='t_user_apple_id_app_key_uidx',
                condition=(
                    models.Q(is_deleted=False)
                    & models.Q(apple_id__isnull=False)
                    & ~models.Q(apple_id='')
                ),
            ),
            models.UniqueConstraint(
                fields=['email', 'app_key'],
                name='t_user_email_app_key_uidx',
                condition=(
                    models.Q(is_deleted=False)
                    & models.Q(email__isnull=False)
                    & ~models.Q(email='')
                ),
            ),
        ]

    def __str__(self):
        return self.username or self.device_id or str(self.id)


class AppConfig(models.Model):
    """后台可配键值（支持 app_key × language × platform 多套）。

    各维空串表示全局；App 读配置时按用户/请求上下文级联回退到全局。
    同维可多行（内容不同）：unique=(key, app_key, language, platform, priority)；
    单读硬规则取 priority 数字更大的一行（见 get_config / filter_scoped_rows）。
    """
    key = models.CharField(max_length=64, verbose_name=_("配置键"))
    value = models.JSONField(default=dict, verbose_name=_("配置值"))  # 可为标量 JSON（int/bool/str）
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))  # 空=全部 App
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))  # 空=全局
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))  # 空=全局
    priority = models.IntegerField(default=0, verbose_name=_("优先级"))  # 同维冲突时数字大优先
    remark = models.CharField(max_length=255, blank=True, default='', verbose_name=_("备注"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_app_config'
        verbose_name = _("应用配置")
        unique_together = [('key', 'app_key', 'language', 'platform', 'priority')]


class Quote(models.Model):
    """语录内容：Feed/主题源；可标 VIP、冷启池、信仰/兴趣标签。

    作用域：app_key×platform 空=全部；language 为正文语言（必填，默认 en）。
    """
    content = models.TextField(verbose_name=_("语录正文"))
    language = models.CharField(max_length=16, default='en', verbose_name=_("语言"))
    # 出处/引用（Assets/Feed 展示；空则客户端不渲染）
    source_ref = models.CharField(max_length=120, blank=True, default='', verbose_name=_("出处引用"))
    # 空=全部 App；非空=仅该应用标识可见
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    # 空=全部设备；ios/android=仅该端可见
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    faith_tags = models.JSONField(default=list, blank=True, verbose_name=_("信仰标签"))
    interest_tags = models.JSONField(default=list, blank=True, verbose_name=_("兴趣标签"))
    content_es = models.TextField(blank=True, default='', verbose_name=_("西语正文"))
    content_pt = models.TextField(blank=True, default='', verbose_name=_("葡语正文"))
    content_type = models.CharField(max_length=80, blank=True, default='', verbose_name=_("内容类型标签"))
    theme = models.CharField(max_length=80, blank=True, default='', verbose_name=_("主主题标签"))
    scenario_tags = models.JSONField(default=list, blank=True, verbose_name=_("生活场景标签"))
    emotion_tags = models.JSONField(default=list, blank=True, verbose_name=_("情绪标签"))
    taxonomy = models.JSONField(default=dict, blank=True, verbose_name=_("自定义类型挂载"))
    display_tags = models.JSONField(default=list, blank=True, verbose_name=_("前端展示标签"))
    tag_index = models.JSONField(default=list, blank=True, verbose_name=_("标签检索键"))
    is_vip = models.BooleanField(default=False, verbose_name=_("VIP内容"))
    # countries 空列表 = 全球可见；非空则按用户 register_country 过滤
    countries = models.JSONField(default=list, blank=True, verbose_name=_("国家限制"))
    audio_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("朗读URL"))
    # 冷启运营池：与兴趣池按 cold_start_*_ratio 混排
    # 物理列名仍为历史 in_ops_pool（库 NOT NULL）；Python 侧统一用 in_cold_start_pool
    in_cold_start_pool = models.BooleanField(
        default=False, db_column='in_ops_pool', verbose_name=_("冷启运营池"),
    )
    sort_weight = models.IntegerField(default=0, verbose_name=_("排序权重"))
    pool_rank_score = models.FloatField(default=0.0, verbose_name=_("同池互动得分"))
    topic_id = models.IntegerField(null=True, blank=True, verbose_name=_("主题ID"))
    is_deleted = models.BooleanField(default=False, verbose_name=_("是否删除"))  # 软删
    status = models.IntegerField(default=1, verbose_name=_("状态"))  # 1 上架
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_quote'
        verbose_name = _("语录")
        ordering = ['-sort_weight', '-id']
        indexes = [
            models.Index(fields=['is_deleted', 'status', 'app_key', 'platform'], name='quote_scope_idx'),
            models.Index(fields=['is_deleted', 'status', 'language'], name='quote_lang_idx'),
        ]


class RecommendStrategy(models.Model):
    """首页命中池策略：仅影响「命中」箱；默认策略不可删不可关。"""

    name = models.CharField(max_length=100, verbose_name=_("策略名称"))
    enabled = models.BooleanField(default=True, verbose_name=_("是否启用"))
    priority = models.IntegerField(default=0, verbose_name=_("优先级"))
    is_default = models.BooleanField(default=False, verbose_name=_("是否默认策略"))
    conditions = models.JSONField(default=list, blank=True, verbose_name=_("适用条件"))
    action = models.JSONField(default=dict, blank=True, verbose_name=_("动作"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    remark = models.CharField(max_length=255, blank=True, default='', verbose_name=_("备注"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_recommend_strategy'
        verbose_name = _("命中策略")
        ordering = ['-priority', 'id']
        indexes = [
            models.Index(fields=['enabled', 'app_key', 'language', 'platform'], name='rec_stg_scope_idx'),
        ]

    def __str__(self):
        return self.name


class QuoteDailyEngage(models.Model):
    """语录按日互动：曝光 / 喜欢 / 分享，供同池得分。"""

    quote_id = models.IntegerField(db_index=True, verbose_name=_("语录ID"))
    day = models.DateField(verbose_name=_("统计日"))
    expose_count = models.IntegerField(default=0, verbose_name=_("曝光次数"))
    like_count = models.IntegerField(default=0, verbose_name=_("喜欢次数"))
    share_count = models.IntegerField(default=0, verbose_name=_("分享次数"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_quote_daily_engage'
        verbose_name = _("语录日互动")
        unique_together = [('quote_id', 'day')]
        indexes = [
            models.Index(fields=['day'], name='quote_engage_day_idx'),
        ]


class Topic(models.Model):
    """主题合集：可会员锁定；语录通过 topic_id 归属。

    作用域：app_key×language×platform 空维=全部可见。
    """
    name = models.CharField(max_length=100, verbose_name=_("主题名"))
    slug = models.CharField(max_length=64, blank=True, default='', verbose_name=_("稳定键"))
    cover_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("封面"))
    description = models.TextField(blank=True, default='', verbose_name=_("描述"))
    is_vip = models.BooleanField(default=False, verbose_name=_("会员锁定"))
    category = models.CharField(max_length=64, blank=True, default='', verbose_name=_("分类"))
    # 空=全部 App / 全部语言 / 全部设备
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    sort_weight = models.IntegerField(default=0, verbose_name=_("排序"))
    is_deleted = models.BooleanField(default=False, verbose_name=_("是否删除"))
    status = models.IntegerField(default=1, verbose_name=_("状态"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_topic'
        verbose_name = _("主题")
        ordering = ['-sort_weight', '-id']
        indexes = [
            models.Index(
                fields=['is_deleted', 'status', 'app_key', 'language', 'platform'],
                name='topic_scope_idx',
            ),
        ]


class Wallpaper(models.Model):
    """首页壁纸资源；is_vip 时需有效会员才能设置。

    作用域：app_key×language×platform 空维=全部可见。
    media_type=static|video（3.0 动态壁纸）；video 时 video_url+poster 必填。
    """
    MEDIA_STATIC = 'static'
    MEDIA_VIDEO = 'video'
    MEDIA_CHOICES = [
        (MEDIA_STATIC, 'Static'),
        (MEDIA_VIDEO, 'Video'),
    ]

    image_url = models.CharField(max_length=512, verbose_name=_("图片URL"))
    media_type = models.CharField(max_length=16, default=MEDIA_STATIC, choices=MEDIA_CHOICES, verbose_name=_("媒体类型"))
    video_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("视频URL"))
    poster_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("视频封面"))
    duration_sec = models.IntegerField(null=True, blank=True, verbose_name=_("视频时长秒"))
    is_vip = models.BooleanField(default=False, verbose_name=_("付费壁纸"))
    tags = models.JSONField(default=list, blank=True, verbose_name=_("标签"))
    name_en = models.CharField(max_length=120, blank=True, default='', verbose_name=_("英文名称"))
    name_es = models.CharField(max_length=120, blank=True, default='', verbose_name=_("西语名称"))
    name_pt = models.CharField(max_length=120, blank=True, default='', verbose_name=_("葡语名称"))
    theme = models.CharField(max_length=80, blank=True, default='', verbose_name=_("主主题标签"))
    visual_tags = models.JSONField(default=list, blank=True, verbose_name=_("视觉风格标签"))
    scenario_tags = models.JSONField(default=list, blank=True, verbose_name=_("生活场景标签"))
    emotion_tags = models.JSONField(default=list, blank=True, verbose_name=_("情绪标签"))
    taxonomy = models.JSONField(default=dict, blank=True, verbose_name=_("自定义类型挂载"))
    display_tags = models.JSONField(default=list, blank=True, verbose_name=_("前端展示标签"))
    tag_index = models.JSONField(default=list, blank=True, verbose_name=_("标签检索键"))
    # 空=全部 App / 全部语言 / 全部设备
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    sort_weight = models.IntegerField(default=0, verbose_name=_("排序"))
    is_deleted = models.BooleanField(default=False, verbose_name=_("是否删除"))
    status = models.IntegerField(default=1, verbose_name=_("状态"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_wallpaper'
        verbose_name = _("壁纸")
        ordering = ['-sort_weight', '-id']
        indexes = [
            models.Index(
                fields=['is_deleted', 'status', 'app_key', 'language', 'platform'],
                name='wallpaper_scope_idx',
            ),
        ]


class UserContentHistory(models.Model):
    """内容曝光/浏览历史，供推荐去重与「我的历史」。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    content_id = models.IntegerField(db_index=True, verbose_name=_("语录ID"))
    action = models.CharField(max_length=32, default='expose', verbose_name=_("动作"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_user_content_history'
        verbose_name = _("内容曝光历史")
        indexes = [models.Index(fields=['user_id', '-created_at'])]


class UserLike(models.Model):
    """用户喜欢；content_id 含义由 target_type 决定；语录喜欢仍驱动新人任务。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    content_id = models.IntegerField(db_index=True, verbose_name=_("内容ID"))
    target_type = models.CharField(
        max_length=16, default='quote', db_index=True, verbose_name=_("目标类型"),
    )  # quote | affirmation
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_user_like'
        verbose_name = _("喜欢")
        unique_together = [('user_id', 'target_type', 'content_id')]


class UserDislike(models.Model):
    """不喜欢记录：推荐侧负反馈，同用户同内容唯一。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    content_id = models.IntegerField(db_index=True, verbose_name=_("语录ID"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_user_dislike'
        unique_together = [('user_id', 'content_id')]


class CollectionFolder(models.Model):
    """用户收藏夹；每人至少一个 is_default 默认夹。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    name = models.CharField(max_length=64, verbose_name=_("夹名"))
    is_default = models.BooleanField(default=False, verbose_name=_("默认夹"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_collection_folder'


class CollectionItem(models.Model):
    """收藏条目；content_id 含义由 target_type 决定。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    folder_id = models.IntegerField(db_index=True, verbose_name=_("收藏夹ID"))
    content_id = models.IntegerField(db_index=True, verbose_name=_("内容ID"))
    target_type = models.CharField(
        max_length=16, default='quote', db_index=True, verbose_name=_("目标类型"),
    )  # quote | affirmation
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_collection_item'
        unique_together = [('user_id', 'target_type', 'content_id')]


class Affirmation(models.Model):
    """自我肯定 1～160 字；3.0 Compose 可挂主题/壁纸/meta（私有，非公域帖）。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    content = models.CharField(max_length=160, verbose_name=_("内容"))
    theme_id = models.IntegerField(null=True, blank=True, verbose_name=_("主题ID"))
    wallpaper_id = models.IntegerField(null=True, blank=True, verbose_name=_("背景壁纸ID"))
    meta = models.JSONField(default=dict, blank=True, verbose_name=_("创作元数据"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_affirmation'


class Quiz(models.Model):
    """Selah 3.0 测验套题；三维 scope 空=全局。"""
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    subtitle = models.CharField(max_length=255, blank=True, default='', verbose_name=_("副标题"))
    cover_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("封面"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    is_vip = models.BooleanField(default=False, verbose_name=_("会员锁"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    is_deleted = models.BooleanField(default=False, verbose_name=_("是否删除"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_quiz'
        ordering = ['sort_order', 'id']
        indexes = [
            models.Index(
                fields=['is_deleted', 'enabled', 'app_key', 'language', 'platform'],
                name='quiz_scope_idx',
            ),
        ]


class QuizQuestion(models.Model):
    """测验题目；options=[{key,text}]；correct_key 不下发给未完成的 start。"""
    quiz_id = models.IntegerField(db_index=True, verbose_name=_("测验ID"))
    stem = models.CharField(max_length=512, verbose_name=_("题干"))
    options = models.JSONField(default=list, blank=True, verbose_name=_("选项"))
    correct_key = models.CharField(max_length=32, verbose_name=_("正确选项key"))
    explain = models.CharField(max_length=512, blank=True, default='', verbose_name=_("解析"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_quiz_question'
        ordering = ['sort_order', 'id']


class QuizAttempt(models.Model):
    """用户测验作答会话。"""
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, '进行中'),
        (STATUS_COMPLETED, '已完成'),
    ]

    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    quiz_id = models.IntegerField(db_index=True, verbose_name=_("测验ID"))
    status = models.CharField(max_length=16, default=STATUS_IN_PROGRESS, choices=STATUS_CHOICES, verbose_name=_("状态"))
    answers = models.JSONField(default=dict, blank=True, verbose_name=_("作答"))
    score = models.IntegerField(default=0, verbose_name=_("得分"))
    total = models.IntegerField(default=0, verbose_name=_("总题数"))
    started_at = models.DateTimeField(auto_now_add=True, verbose_name=_("开始时间"))
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("完成时间"))

    class Meta:
        db_table = 't_quiz_attempt'
        indexes = [
            models.Index(fields=['user_id', 'quiz_id', '-started_at'], name='quiz_attempt_user_idx'),
        ]


class AppIcon(models.Model):
    """App Alternate Icon 目录；icon_key 对齐原生资源名。"""
    icon_key = models.CharField(max_length=64, verbose_name=_("图标key"))
    title = models.CharField(max_length=64, verbose_name=_("标题"))
    preview_url = models.CharField(max_length=512, verbose_name=_("预览图"))
    is_vip = models.BooleanField(default=False, verbose_name=_("会员锁"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    is_deleted = models.BooleanField(default=False, verbose_name=_("是否删除"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_app_icon'
        ordering = ['sort_order', 'id']
        indexes = [
            models.Index(
                fields=['is_deleted', 'enabled', 'app_key', 'language', 'platform'],
                name='app_icon_scope_idx',
            ),
        ]


class AffirmationTemplate(models.Model):
    """运营预置肯定语模板；App 空态可一键填入。

    作用域：app_key×language×platform 空维=全局。
    """
    content = models.CharField(max_length=160, verbose_name=_("模板正文"))
    sort_weight = models.IntegerField(default=0, verbose_name=_("排序权重"))
    status = models.IntegerField(default=1, verbose_name=_("状态"))  # 1 启用
    is_deleted = models.BooleanField(default=False, verbose_name=_("是否删除"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_affirmation_template'
        verbose_name = _("肯定语模板")
        ordering = ['-sort_weight', '-id']
        indexes = [
            models.Index(
                fields=['is_deleted', 'status', 'app_key', 'language', 'platform'],
                name='aff_tpl_scope_idx',
            ),
        ]


class WidgetCard(models.Model):
    """小组件卡片：每组「文字 + 描述 + 图片」；与旧 widget-payload 无关。

    作用域：app_key×language×platform 空维=全局。
    image_type：square=方图，wide=长图。
    """
    IMAGE_TYPE_SQUARE = 'square'
    IMAGE_TYPE_WIDE = 'wide'
    IMAGE_TYPE_CHOICES = (
        (IMAGE_TYPE_SQUARE, _('方')),
        (IMAGE_TYPE_WIDE, _('长')),
    )

    text = models.CharField(max_length=256, verbose_name=_("文字"))
    description = models.CharField(max_length=512, blank=True, default='', verbose_name=_("描述"))
    image_url = models.CharField(max_length=512, verbose_name=_("图片URL"))
    image_type = models.CharField(
        max_length=16, default=IMAGE_TYPE_SQUARE, choices=IMAGE_TYPE_CHOICES,
        verbose_name=_("图片类型"),
    )
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    is_deleted = models.BooleanField(default=False, verbose_name=_("是否删除"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_widget_card'
        verbose_name = _("小组件卡片")
        ordering = ['sort_order', 'id']
        indexes = [
            models.Index(
                fields=['is_deleted', 'enabled', 'app_key', 'language', 'platform'],
                name='widget_card_scope_idx',
            ),
        ]


class TopicFollow(models.Model):
    """用户关注主题；同用户同主题唯一。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    topic_id = models.IntegerField(db_index=True, verbose_name=_("主题ID"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_topic_follow'
        unique_together = [('user_id', 'topic_id')]

class FolderFollow(models.Model):
    """用户关注自己的收藏夹；同用户同夹唯一。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    folder_id = models.IntegerField(db_index=True, verbose_name=_("收藏夹ID"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_folder_follow'
        unique_together = [('user_id', 'folder_id')]


class AffirmationFollow(models.Model):
    """用户关注自己的自我肯定；同用户同肯定语唯一。关注后可混入本人 Feed。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    affirmation_id = models.IntegerField(db_index=True, verbose_name=_("自我肯定ID"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_affirmation_follow'
        unique_together = [('user_id', 'affirmation_id')]


class WallpaperUsage(models.Model):
    """用户曾设置过的壁纸；同用户同壁纸唯一，按 last_used_at 排序。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    wallpaper_id = models.IntegerField(db_index=True, verbose_name=_("壁纸ID"))
    last_used_at = models.DateTimeField(auto_now=True, verbose_name=_("最近使用时间"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_wallpaper_usage'
        unique_together = [('user_id', 'wallpaper_id')]
        ordering = ['-last_used_at', '-id']


class CheckinRecord(models.Model):
    """打卡日记录：checked=实际打勾，frozen=爱心冻结漏打日；按用户本地自然日唯一。

    frozen 日不增加 checkin_streak，也不发额度；granted_quota/hit_cap 仅 checked 有意义。
    """
    STATUS_CHECKED = 'checked'
    STATUS_FROZEN = 'frozen'
    STATUS_CHOICES = [
        (STATUS_CHECKED, '已打卡'),
        (STATUS_FROZEN, '冻结'),
    ]

    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    local_date = models.DateField(verbose_name=_("本地自然日"))
    status = models.CharField(max_length=16, default=STATUS_CHECKED, choices=STATUS_CHOICES, verbose_name=_("状态"))
    granted_quota = models.IntegerField(default=0, verbose_name=_("发放额度"))  # 本次实际加上的 free 次
    hit_cap = models.BooleanField(default=False, verbose_name=_("是否触顶"))  # 因 free_quota_cap 未发满
    vip_level = models.CharField(max_length=20, default='free', verbose_name=_("当时会员"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_checkin_record'
        unique_together = [('user_id', 'local_date')]


class OnboardingQuestion(models.Model):
    """冷启问卷题；按 language×platform 多套，空维度=全局。

    question_key 同键多维度时由 filter_scoped_rows 选最匹配一条。
    """
    question_key = models.CharField(max_length=64, verbose_name=_("题目标识"))
    title = models.CharField(max_length=255, verbose_name=_("标题"))
    options = models.JSONField(default=list, verbose_name=_("选项"))
    sort_order = models.IntegerField(default=0, verbose_name=_("顺序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_onboarding_question'
        ordering = ['sort_order']
        unique_together = [('question_key', 'app_key', 'language', 'platform')]


class WelcomeScreen(models.Model):
    """冷启全屏欢迎页；按 app_key×language×platform 多套，空维度=全局。

    version 上调后客户端可再次展示（对比本地 welcome_seen_version）。
    Terms/Privacy URL 仍走 AppConfig，不在此表维护。
    同维多行时 App 单读取 priority 更大者（同分再看 sort_order）。
    """
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    subtitle = models.CharField(max_length=255, blank=True, default='', verbose_name=_("副标题"))
    cta_text = models.CharField(max_length=64, blank=True, default='Continue', verbose_name=_("按钮文案"))
    image_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("背景图URL"))
    version = models.IntegerField(default=1, verbose_name=_("版本"))  # 运营改版时可 +1 强制再看
    priority = models.IntegerField(default=0, verbose_name=_("优先级"))  # 同维冲突时数字大优先
    sort_order = models.IntegerField(default=0, verbose_name=_("顺序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_welcome_screen'
        ordering = ['-priority', 'sort_order', 'id']
        verbose_name = _("欢迎页")


class InterstitialScreen(models.Model):
    """2.0 教育夹层（试用说明/价值教育等）；按 kind + 三维 scope。

    与 WelcomeScreen 分离，避免与冷启 version 门闸耦合。
    客户端本地键：interstitial_seen_{kind}_v{version}。
    同 kind + 同维多行时 App 单读取 priority 更大者。
    """
    kind = models.CharField(max_length=32, verbose_name=_("夹层类型"))
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    subtitle = models.CharField(max_length=255, blank=True, default='', verbose_name=_("副标题"))
    cta_text = models.CharField(max_length=64, blank=True, default='Continue', verbose_name=_("按钮文案"))
    image_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("背景图URL"))
    version = models.IntegerField(default=1, verbose_name=_("版本"))
    priority = models.IntegerField(default=0, verbose_name=_("优先级"))  # 同维冲突时数字大优先
    sort_order = models.IntegerField(default=0, verbose_name=_("顺序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_interstitial_screen'
        ordering = ['kind', '-priority', 'sort_order', 'id']
        verbose_name = _("教育夹层")
        indexes = [
            models.Index(
                fields=['enabled', 'kind', 'app_key', 'language', 'platform'],
                name='interstitial_scope_idx',
            ),
        ]


class AiConversation(models.Model):
    """AI 会话：按用户本地日一条会话容器。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    local_date = models.DateField(verbose_name=_("本地日"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_ai_conversation'


class AiMessage(models.Model):
    """AI 消息；安全拒答不扣次，正常回复才标 deducted。

    safety_blocked=True 的助手消息不进 LLM 上下文回溯，避免拒答文案污染对话。
    """
    conversation_id = models.IntegerField(db_index=True, verbose_name=_("会话ID"))
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    role = models.CharField(max_length=16, verbose_name=_("角色"))  # user/assistant/system
    content = models.TextField(verbose_name=_("内容"))
    deducted = models.BooleanField(default=False, verbose_name=_("已扣次"))  # 仅成功助手回复可为 True
    safety_blocked = models.BooleanField(default=False, verbose_name=_("安全拒答"))
    safety_category = models.CharField(max_length=32, blank=True, default='', verbose_name=_("安全类别"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_ai_message'
        ordering = ['id']


class AiSafetyAudit(models.Model):
    """AI 安全命中审计日志（运营台可查）。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    safety_category = models.CharField(max_length=32, verbose_name=_("类别"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("时间"))

    class Meta:
        db_table = 't_ai_safety_audit'


class AiSafetyKeyword(models.Model):
    """安全关键词；按类别开关 + app/语言/平台作用域。"""
    category = models.CharField(max_length=32, verbose_name=_("类别"))
    keyword = models.CharField(max_length=128, verbose_name=_("关键词"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))

    class Meta:
        db_table = 't_ai_safety_keyword'


class CrisisHotline(models.Model):
    """危机热线：自残类拒答时按国家回传给客户端。

    country 可用 GLOBAL 作兜底；查询时常 OR 用户国 / US / GLOBAL。
    """
    country = models.CharField(max_length=8, verbose_name=_("国家"))
    label = models.CharField(max_length=128, verbose_name=_("标签"))
    phone_or_url = models.CharField(max_length=128, verbose_name=_("电话或链接"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))

    class Meta:
        db_table = 't_crisis_hotline'


class AiQuickPrompt(models.Model):
    """AI 快捷开场文案（可按 app/语言/平台作用域）。"""
    title = models.CharField(max_length=100, verbose_name=_("标题"))
    prompt = models.TextField(verbose_name=_("开场文案"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))

    class Meta:
        db_table = 't_ai_quick_prompt'
        ordering = ['sort_order']


class ContentReport(models.Model):
    """用户举报语录；运营台处理 pending→handled/dismissed。"""
    STATUS_PENDING = 'pending'
    STATUS_HANDLED = 'handled'
    STATUS_DISMISSED = 'dismissed'
    STATUS_CHOICES = [
        (STATUS_PENDING, '待处理'),
        (STATUS_HANDLED, '已处理'),
        (STATUS_DISMISSED, '已忽略'),
    ]

    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    content_id = models.IntegerField(db_index=True, verbose_name=_("语录ID"))
    reason = models.CharField(max_length=64, verbose_name=_("原因"))
    detail = models.TextField(blank=True, default='', verbose_name=_("详情"))
    status = models.CharField(
        max_length=16, default=STATUS_PENDING, choices=STATUS_CHOICES, verbose_name=_("处理状态"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_content_report'


class IapReceipt(models.Model):
    """IAP 收据与订单元数据；transaction_id 全局唯一，支持恢复购买。

    status: active|expired|refunded|revoked；original_transaction_id 用于续订族恢复。
    """
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', db_index=True, verbose_name=_("应用标识"))
    platform = models.CharField(max_length=16, verbose_name=_("平台"))  # ios/android
    product_id = models.CharField(max_length=128, verbose_name=_("商品ID"))
    transaction_id = models.CharField(max_length=128, unique=True, verbose_name=_("交易ID"))
    # 订阅族根交易：restore 时可按此找回最新 active
    original_transaction_id = models.CharField(max_length=128, blank=True, default='', verbose_name=_("原始交易ID"))
    receipt_data = models.TextField(blank=True, default='', verbose_name=_("收据"))
    is_trial = models.BooleanField(default=False, verbose_name=_("试用"))
    amount_cents = models.IntegerField(default=0, verbose_name=_("成交金额(分)"))  # 试用记 0
    currency = models.CharField(max_length=8, blank=True, default='USD', verbose_name=_("币种"))
    expire_at = models.DateTimeField(null=True, blank=True, verbose_name=_("到期UTC"))
    status = models.CharField(max_length=32, default='active', verbose_name=_("状态"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_iap_receipt'


class MembershipProduct(models.Model):
    """会员商品：商店 product_id 与 App plans 联动。

    trial_days 非空时覆盖全局 trial_days；enabled=False 不对 App 露出。
    app_key 区分品牌 SKU（空=遗留全局，App 单读优先精确 App 再回落空维）。
    """

    PLAN_MONTHLY = 'monthly'
    PLAN_YEARLY = 'yearly'
    PLAN_CHOICES = [
        (PLAN_MONTHLY, '月度'),
        (PLAN_YEARLY, '年度'),
    ]
    PLATFORM_IOS = 'ios'
    PLATFORM_ANDROID = 'android'
    PLATFORM_CHOICES = [
        (PLATFORM_IOS, 'iOS'),
        (PLATFORM_ANDROID, 'Android'),
    ]

    app_key = models.CharField(max_length=32, blank=True, default='', db_index=True, verbose_name=_("应用标识"))
    plan_type = models.CharField(max_length=16, choices=PLAN_CHOICES, verbose_name=_("套餐类型"))
    platform = models.CharField(max_length=16, choices=PLATFORM_CHOICES, verbose_name=_("平台"))
    store_product_id = models.CharField(max_length=128, verbose_name=_("商店商品ID"))
    title = models.CharField(max_length=128, blank=True, default='', verbose_name=_("展示标题"))
    price_display = models.CharField(max_length=64, blank=True, default='', verbose_name=_("价格文案"))
    # 多语言价格文案：{"en":"$39.99/yr","zh":"¥298/年"}；App 按当前语言回落 price_display
    price_display_i18n = models.JSONField(default=dict, blank=True, verbose_name=_("价格文案多语言"))
    price_cents = models.IntegerField(default=0, verbose_name=_("成交价(分)"))
    currency = models.CharField(max_length=8, blank=True, default='USD', verbose_name=_("币种"))
    duration_days = models.IntegerField(default=30, verbose_name=_("开通天数"))
    trial_days = models.IntegerField(null=True, blank=True, verbose_name=_("试用天数覆盖"))  # None=用 AppConfig.trial_days
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    sort_weight = models.IntegerField(default=0, verbose_name=_("排序权重"))
    remark = models.CharField(max_length=255, blank=True, default='', verbose_name=_("备注"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_membership_product'
        verbose_name = _("会员商品")
        unique_together = [('app_key', 'platform', 'store_product_id')]
        ordering = ['-sort_weight', 'plan_type', 'platform', 'id']

    def __str__(self):
        return f'{self.app_key or "*"}:{self.platform}:{self.plan_type}:{self.store_product_id}'


class AnalyticsEvent(models.Model):
    """App 埋点入库（与 Firebase §9.4 事件名一致）"""

    user_id = models.IntegerField(null=True, blank=True, db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', db_index=True, verbose_name=_("应用标识"))
    event_name = models.CharField(max_length=64, db_index=True, verbose_name=_("事件名"))
    event_date = models.DateField(db_index=True, verbose_name=_("事件UTC日"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    system_lang = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    register_country = models.CharField(max_length=8, blank=True, default='', verbose_name=_("国家"))
    params = models.JSONField(default=dict, blank=True, verbose_name=_("事件参数"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_analytics_event'
        verbose_name = _("埋点事件")
        indexes = [
            models.Index(fields=['event_date', 'event_name']),
            models.Index(fields=['user_id', 'event_date']),
            models.Index(fields=['app_key', 'event_date'], name='t_analytic_app_key_event_d_idx'),
        ]


class MannaWallet(models.Model):
    """Manna 虚拟货币钱包（按 user × app_key；≠ Selah AI quota / 打卡爱心）。"""

    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, default='manna', verbose_name=_("应用标识"))
    balance = models.IntegerField(default=0, verbose_name=_("余额"))
    stats = models.JSONField(default=dict, blank=True, verbose_name=_("四维统计缓存"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_manna_wallet'
        verbose_name = _("Manna钱包")
        unique_together = [('user_id', 'app_key')]

    def __str__(self):
        return f'user={self.user_id} app={self.app_key} bal={self.balance}'


class MannaLedger(models.Model):
    """Manna 货币流水（可审计；改余额必须写 ledger）。"""

    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, default='manna', db_index=True, verbose_name=_("应用标识"))
    delta = models.IntegerField(verbose_name=_("变动"))
    balance_after = models.IntegerField(verbose_name=_("变动后余额"))
    reason = models.CharField(max_length=64, verbose_name=_("原因"))
    ref_type = models.CharField(max_length=32, blank=True, default='', verbose_name=_("关联类型"))
    ref_id = models.CharField(max_length=64, blank=True, default='', verbose_name=_("关联ID"))
    remark = models.CharField(max_length=255, blank=True, default='', verbose_name=_("备注"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_manna_ledger'
        verbose_name = _("Manna流水")
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['user_id', 'app_key', 'created_at'], name='manna_ledger_user_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user_id', 'app_key', 'reason', 'ref_id'],
                name='t_manna_ledger_user_reason_ref_uidx',
                condition=~models.Q(ref_id=''),
            ),
        ]

    def __str__(self):
        return f'user={self.user_id} {self.reason} {self.delta}'


class VerseCardTheme(models.Model):
    """Verse card 底栏主题图（运营配置，禁止客户端写死列表）。

    同 app_key×language×platform 下 theme_key 唯一；空维=全端可见。
    """
    theme_key = models.CharField(max_length=64, verbose_name=_("主题键"))
    image_url = models.CharField(max_length=512, verbose_name=_("背景图URL"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    is_default = models.BooleanField(default=False, verbose_name=_("默认选中"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_versecard_theme'
        ordering = ['sort_order', 'id']
        verbose_name = _("Verse card 主题")
        indexes = [
            models.Index(
                fields=['enabled', 'app_key', 'language', 'platform'],
                name='versecard_theme_scope_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['theme_key', 'app_key', 'language', 'platform'],
                name='versecard_theme_key_scope_uidx',
            ),
        ]

    def __str__(self):
        return f'{self.theme_key} @{self.app_key or "*"}'


class DailyVerse(models.Model):
    """Manna 今日经文运营位。"""
    verse_text = models.TextField(verbose_name=_("经文正文"))
    reference = models.CharField(max_length=128, blank=True, default='', verbose_name=_("出处"))
    bible_book = models.CharField(max_length=64, blank=True, default='', verbose_name=_("书卷key"))
    chapter = models.IntegerField(null=True, blank=True, verbose_name=_("章"))
    verse_start = models.IntegerField(null=True, blank=True, verbose_name=_("节起"))
    verse_end = models.IntegerField(null=True, blank=True, verbose_name=_("节止"))
    prayer_title = models.CharField(max_length=128, blank=True, default='', verbose_name=_("祷告标题"))
    prayer_body = models.TextField(blank=True, default='', verbose_name=_("祷告文"))
    wallpaper_id = models.IntegerField(null=True, blank=True, verbose_name=_("壁纸ID"))
    image_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("日间背景图"))
    image_url_night = models.CharField(max_length=512, blank=True, default='', verbose_name=_("夜间背景图"))
    active_date = models.DateField(null=True, blank=True, db_index=True, verbose_name=_("生效日UTC"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_daily_verse'
        ordering = ['-active_date', 'sort_order', 'id']
        indexes = [
            models.Index(
                fields=['enabled', 'app_key', 'language', 'platform'],
                name='daily_verse_scope_idx',
            ),
        ]


class BibleTranslation(models.Model):
    """圣经译本。"""
    code = models.CharField(max_length=32, verbose_name=_("译本代码"))
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_bible_translation'
        ordering = ['sort_order', 'id']
        unique_together = [('code', 'app_key', 'language', 'platform')]


class BibleBook(models.Model):
    """圣经书卷目录。"""
    translation_id = models.IntegerField(db_index=True, verbose_name=_("译本ID"))
    book_key = models.CharField(max_length=64, verbose_name=_("书卷key"))
    name = models.CharField(max_length=128, verbose_name=_("书卷名"))
    order_index = models.IntegerField(default=0, verbose_name=_("顺序"))
    testament = models.CharField(max_length=16, blank=True, default='ot', verbose_name=_("约"))
    tag_keys = models.JSONField(default=list, blank=True, verbose_name=_("Tag键列表"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_bible_book'
        ordering = ['order_index', 'id']
        unique_together = [('translation_id', 'book_key')]
        indexes = [
            models.Index(fields=['translation_id', 'order_index'], name='bible_book_tr_idx'),
        ]


class BibleChapter(models.Model):
    """圣经章（含听经元数据）。"""
    book_id = models.IntegerField(db_index=True, verbose_name=_("书卷ID"))
    chapter_num = models.IntegerField(verbose_name=_("章号"))
    audio_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("音频URL"))
    audio_duration_sec = models.IntegerField(default=0, verbose_name=_("音频秒数"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_bible_chapter'
        ordering = ['chapter_num', 'id']
        unique_together = [('book_id', 'chapter_num')]


class BibleVerse(models.Model):
    """圣经节。"""
    chapter_id = models.IntegerField(db_index=True, verbose_name=_("章ID"))
    verse_num = models.IntegerField(verbose_name=_("节号"))
    text = models.TextField(verbose_name=_("经文"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_bible_verse'
        ordering = ['verse_num', 'id']
        unique_together = [('chapter_id', 'verse_num')]


class BibleHighlight(models.Model):
    """用户圣经高亮。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    translation_code = models.CharField(max_length=32, blank=True, default='', verbose_name=_("译本"))
    book_key = models.CharField(max_length=64, verbose_name=_("书卷"))
    chapter = models.IntegerField(verbose_name=_("章"))
    verse = models.IntegerField(verbose_name=_("节"))
    color = models.CharField(max_length=32, blank=True, default='', verbose_name=_("颜色"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_bible_highlight'
        indexes = [
            models.Index(fields=['user_id', 'app_key', 'book_key'], name='bible_hl_user_idx'),
        ]


class BibleNote(models.Model):
    """用户圣经笔记。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    translation_code = models.CharField(max_length=32, blank=True, default='', verbose_name=_("译本"))
    book_key = models.CharField(max_length=64, verbose_name=_("书卷"))
    chapter = models.IntegerField(verbose_name=_("章"))
    verse = models.IntegerField(verbose_name=_("节"))
    body = models.TextField(verbose_name=_("笔记正文"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_bible_note'
        indexes = [
            models.Index(fields=['user_id', 'app_key'], name='bible_note_user_idx'),
        ]


class BibleAmen(models.Model):
    """用户经文 Amen 记录。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    translation_code = models.CharField(max_length=32, blank=True, default='', verbose_name=_("译本"))
    book_key = models.CharField(max_length=64, blank=True, default='', verbose_name=_("书卷"))
    chapter = models.IntegerField(null=True, blank=True, verbose_name=_("章"))
    verse = models.IntegerField(null=True, blank=True, verbose_name=_("节"))
    scene = models.CharField(max_length=64, blank=True, default='', verbose_name=_("场景"))
    ref = models.CharField(max_length=255, blank=True, default='', verbose_name=_("引用"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_bible_amen'
        indexes = [
            models.Index(fields=['user_id', 'app_key', 'created_at'], name='bible_amen_user_idx'),
        ]


class DevotionSeries(models.Model):
    """灵修系列。"""
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    cover_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("封面"))
    # 系列详情 about 区：牧师名下方简介
    pastor_bio = models.CharField(max_length=255, blank=True, default='', verbose_name=_("牧师简介"))
    pastor_name = models.CharField(max_length=128, blank=True, default='', verbose_name=_("作者/牧师名"))
    pastor_avatar_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("作者头像URL"))
    tags = models.JSONField(default=list, blank=True, verbose_name=_("标签"))
    session_count = models.IntegerField(default=0, verbose_name=_("课时数"))
    is_vip = models.BooleanField(default=False, verbose_name=_("会员锁"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_devotion_series'
        ordering = ['sort_order', 'id']


class DevotionSession(models.Model):
    """灵修课时。"""
    series_id = models.IntegerField(db_index=True, verbose_name=_("系列ID"))
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    scripture_ref = models.CharField(max_length=128, blank=True, default='', verbose_name=_("经文出处"))
    scripture_text = models.TextField(blank=True, default='', verbose_name=_("经文"))
    body = models.TextField(verbose_name=_("正文"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    duration_hint_sec = models.IntegerField(default=0, verbose_name=_("建议时长秒"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_devotion_session'
        ordering = ['sort_order', 'id']


class DevotionProgress(models.Model):
    """用户灵修进度。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    series_id = models.IntegerField(db_index=True, verbose_name=_("系列ID"))
    completed_session_ids = models.JSONField(default=list, blank=True, verbose_name=_("已完成课时"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_devotion_progress'
        unique_together = [('user_id', 'app_key', 'series_id')]


class MannaQuizLevel(models.Model):
    """Manna 测验关卡（与 Selah t_quiz 隔离）。"""
    level_no = models.IntegerField(default=1, verbose_name=_("关卡号"))
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    time_limit_sec = models.IntegerField(default=0, verbose_name=_("限时秒"))
    pass_score = models.IntegerField(default=1, verbose_name=_("通关分"))
    reward_manna = models.IntegerField(default=0, verbose_name=_("通关奖励"))
    fail_penalty = models.IntegerField(default=0, verbose_name=_("失败扣币"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_manna_quiz_level'
        ordering = ['sort_order', 'level_no', 'id']


class MannaQuizQuestion(models.Model):
    """Manna 测验题目。"""
    QTYPE_SINGLE = 'single'
    QTYPE_BOOL = 'bool'
    QTYPE_CHOICES = [
        (QTYPE_SINGLE, '单选'),
        (QTYPE_BOOL, '判断'),
    ]

    level_id = models.IntegerField(db_index=True, verbose_name=_("关卡ID"))
    qtype = models.CharField(max_length=16, default=QTYPE_SINGLE, choices=QTYPE_CHOICES, verbose_name=_("题型"))
    stem = models.CharField(max_length=512, verbose_name=_("题干"))
    options = models.JSONField(default=list, blank=True, verbose_name=_("选项"))
    answer_key = models.CharField(max_length=32, verbose_name=_("答案key"))
    bible_ref = models.JSONField(default=dict, blank=True, verbose_name=_("经文锚点"))
    explanation = models.CharField(max_length=512, blank=True, default='', verbose_name=_("解析"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_manna_quiz_question'
        ordering = ['sort_order', 'id']


class MannaQuizStory(models.Model):
    """Manna 故事卡（测验解锁）。"""
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    cover_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("学习页背景"))
    card_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("故事卡图"))
    unlock_cost = models.IntegerField(default=0, verbose_name=_("解锁花费"))
    knowledge_cards = models.JSONField(default=list, blank=True, verbose_name=_("知识点"))
    linked_level_id = models.IntegerField(null=True, blank=True, verbose_name=_("关联关卡"))
    body = models.TextField(blank=True, default='', verbose_name=_("正文"))
    # 有序图文块：[{type:text,text}|{type:image,url,alt?}]；封面取第 cover_image_index 张图
    blocks = models.JSONField(default=list, blank=True, verbose_name=_("图文块"))
    cover_image_index = models.PositiveSmallIntegerField(default=0, verbose_name=_("封面插图序号"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_manna_quiz_story'
        ordering = ['sort_order', 'id']


class MannaQuizAttempt(models.Model):
    """Manna 测验作答会话。"""
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, '进行中'),
        (STATUS_COMPLETED, '已完成'),
    ]

    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    level_id = models.IntegerField(db_index=True, verbose_name=_("关卡ID"))
    status = models.CharField(max_length=16, default=STATUS_IN_PROGRESS, choices=STATUS_CHOICES, verbose_name=_("状态"))
    score = models.IntegerField(default=0, verbose_name=_("得分"))
    total = models.IntegerField(default=0, verbose_name=_("总题数"))
    manna_delta = models.IntegerField(default=0, verbose_name=_("Manna变动"))
    detail = models.JSONField(default=dict, blank=True, verbose_name=_("作答明细"))
    started_at = models.DateTimeField(auto_now_add=True, verbose_name=_("开始时间"))
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name=_("结束时间"))

    class Meta:
        db_table = 't_manna_quiz_attempt'
        indexes = [
            models.Index(fields=['user_id', 'level_id', '-started_at'], name='manna_quiz_att_idx'),
        ]


class MannaQuizStoryUnlock(models.Model):
    """用户已解锁故事。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    story_id = models.IntegerField(db_index=True, verbose_name=_("故事ID"))
    unlocked_at = models.DateTimeField(auto_now_add=True, verbose_name=_("解锁时间"))

    class Meta:
        db_table = 't_manna_quiz_story_unlock'
        unique_together = [('user_id', 'app_key', 'story_id')]


class ActivityDay(models.Model):
    """用户活动日汇总（≠ Selah checkin）。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    date = models.DateField(db_index=True, verbose_name=_("日期UTC"))
    flags = models.JSONField(default=dict, blank=True, verbose_name=_("日标记"))
    pray_sec = models.IntegerField(default=0, verbose_name=_("祷告秒"))
    devotion_sec = models.IntegerField(default=0, verbose_name=_("灵修秒"))
    read_sec = models.IntegerField(default=0, verbose_name=_("阅读秒"))
    quiz_count = models.IntegerField(default=0, verbose_name=_("测验次数"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_activity_day'
        unique_together = [('user_id', 'app_key', 'date')]
        indexes = [
            models.Index(fields=['user_id', 'app_key', 'date'], name='activity_day_user_idx'),
        ]


class BadgeDef(models.Model):
    """徽章定义。"""
    code = models.CharField(max_length=64, verbose_name=_("徽章码"))
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    icon_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("图标"))
    rule = models.JSONField(default=dict, blank=True, verbose_name=_("规则"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_badge_def'
        ordering = ['sort_order', 'id']
        unique_together = [('code', 'app_key', 'language', 'platform')]


class BadgeUser(models.Model):
    """用户已获徽章。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    badge_id = models.IntegerField(db_index=True, verbose_name=_("徽章ID"))
    earned_at = models.DateTimeField(auto_now_add=True, verbose_name=_("获得时间"))

    class Meta:
        db_table = 't_badge_user'
        unique_together = [('user_id', 'app_key', 'badge_id')]


class PrayerContent(models.Model):
    """晨晚祷内容。"""
    KIND_MORNING = 'morning'
    KIND_NIGHT = 'night'
    KIND_CHOICES = [
        (KIND_MORNING, '晨祷'),
        (KIND_NIGHT, '晚祷'),
    ]

    kind = models.CharField(max_length=16, choices=KIND_CHOICES, verbose_name=_("类型"))
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    body = models.TextField(verbose_name=_("正文"))
    verse_text = models.TextField(blank=True, default='', verbose_name=_("经文"))
    reference = models.CharField(max_length=128, blank=True, default='', verbose_name=_("出处"))
    image_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("背景图URL"))
    version = models.IntegerField(default=1, verbose_name=_("版本"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_prayer_content'
        ordering = ['kind', 'sort_order', 'id']


class MannaFavorite(models.Model):
    """Manna 喜欢/收藏（2.0）。"""
    user_id = models.IntegerField(db_index=True, verbose_name=_("用户ID"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    target_type = models.CharField(max_length=32, verbose_name=_("目标类型"))
    target_key = models.CharField(max_length=255, verbose_name=_("目标键"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        db_table = 't_manna_favorite'
        unique_together = [('user_id', 'app_key', 'target_type', 'target_key')]


class MannaHomeCard(models.Model):
    """Home 运营卡（2.0）。标题/副标题按 app×语言×设备下发；跳转走 link_path。"""
    card_type = models.CharField(max_length=32, blank=True, default='', verbose_name=_("卡片类型"))
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    subtitle = models.CharField(max_length=255, blank=True, default='', verbose_name=_("副标题"))
    cover_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("封面"))
    # uni-app 产品内路径，如 /pages/quiz/quiz；详情页用 payload.target_id 拼 ?id=
    link_path = models.CharField(max_length=255, blank=True, default='', verbose_name=_("跳转路径"))
    payload = models.JSONField(default=dict, blank=True, verbose_name=_("载荷"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    active_date = models.DateField(null=True, blank=True, db_index=True, verbose_name=_("生效日"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_manna_home_card'
        ordering = ['sort_order', 'id']


class MannaTestimony(models.Model):
    """Testimony 内容（2.0）。"""
    title = models.CharField(max_length=128, verbose_name=_("标题"))
    body = models.TextField(verbose_name=_("正文"))
    author_label = models.CharField(max_length=128, blank=True, default='', verbose_name=_("作者标签"))
    cover_url = models.CharField(max_length=512, blank=True, default='', verbose_name=_("封面"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_manna_testimony'
        ordering = ['sort_order', 'id']


class MannaStatsCopy(models.Model):
    """成长卡文案（2.0）。"""
    line = models.CharField(max_length=255, verbose_name=_("文案"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_manna_stats_copy'
        ordering = ['sort_order', 'id']


class MannaContentTag(models.Model):
    """内容 Tag 字典（2.0）。"""
    key = models.CharField(max_length=64, verbose_name=_("键"))
    display_name = models.CharField(max_length=128, verbose_name=_("展示名"))
    # 该 Tag 下运营指定的推荐灵修系列；空=后端按未学过自动回退
    featured_series_id = models.IntegerField(null=True, blank=True, verbose_name=_("推荐系列ID"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    app_key = models.CharField(max_length=32, blank=True, default='', verbose_name=_("应用标识"))
    language = models.CharField(max_length=16, blank=True, default='', verbose_name=_("语言"))
    platform = models.CharField(max_length=16, blank=True, default='', verbose_name=_("设备平台"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_manna_content_tag'
        ordering = ['sort_order', 'id']
        unique_together = [('key', 'app_key', 'language', 'platform')]


class TagType(models.Model):
    """后台归类标签类型：适用范围、上传必填、单选/多选、系统锁定。"""

    SCOPE_TEXT = 'text'
    SCOPE_WALLPAPER = 'wallpaper'
    SCOPE_BOTH = 'both'
    SCOPE_CHOICES = [
        (SCOPE_TEXT, '文字'),
        (SCOPE_WALLPAPER, '壁纸'),
        (SCOPE_BOTH, '文字／壁纸'),
    ]

    key = models.CharField(max_length=64, unique=True, verbose_name=_("类型键"))
    label = models.CharField(max_length=80, verbose_name=_("英文名称"))
    prefix = models.CharField(max_length=8, default='L', verbose_name=_("ID 前缀"))
    scope = models.CharField(max_length=16, default=SCOPE_BOTH, choices=SCOPE_CHOICES, verbose_name=_("适用内容"))
    required = models.BooleanField(default=False, verbose_name=_("上传必填"))
    is_multi = models.BooleanField(default=False, verbose_name=_("多选"))  # True=多选(JSON list) / False=单选(CharField)
    locked = models.BooleanField(default=False, verbose_name=_("系统固定"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_tag_type'
        verbose_name = _("标签类型")
        ordering = ['sort_order', 'id']


class Tag(models.Model):
    """后台归类标签值；名称统一英文。"""

    type_key = models.CharField(max_length=64, db_index=True, verbose_name=_("类型键"))
    code = models.CharField(max_length=16, blank=True, default='', verbose_name=_("展示 ID"))
    name = models.CharField(max_length=80, verbose_name=_("英文名称"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))
    related_themes = models.JSONField(default=list, blank=True, verbose_name=_("关联主题"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_tag'
        verbose_name = _("归类标签")
        ordering = ['type_key', 'id']
        unique_together = [('type_key', 'name')]
        indexes = [
            models.Index(fields=['type_key', 'enabled'], name='tag_type_enabled_idx'),
        ]


class DisplayTag(models.Model):
    """显性标签编辑态：文字模块/合集、壁纸合集。发布后由 DisplayStructureState 快照生效。"""

    KIND_QUOTE = 'quote'
    KIND_WALLPAPER = 'wallpaper'
    KIND_CHOICES = [
        (KIND_QUOTE, '语录'),
        (KIND_WALLPAPER, '壁纸'),
    ]
    TYPE_COLUMN = 'column'
    TYPE_COLLECTION = 'collection'
    TYPE_COLLECTION_GROUP = 'collection_group'
    TYPE_FILTER = 'filter'
    MODE_AUTO = 'auto'
    MODE_MANUAL = 'manual'
    MATCH_ANY = 'any'
    MATCH_ALL = 'all'

    kind = models.CharField(max_length=16, choices=KIND_CHOICES, verbose_name=_("内容种类"))
    display_type = models.CharField(max_length=24, default=TYPE_COLLECTION, verbose_name=_("显性对象类型"))
    parent_id = models.IntegerField(null=True, blank=True, verbose_name=_("所属模块ID"))
    source_tag_id = models.IntegerField(null=True, blank=True, verbose_name=_("来源隐性标签ID"))
    name_en = models.CharField(max_length=80, verbose_name=_("英文展示名"))
    name_es = models.CharField(max_length=80, blank=True, default='', verbose_name=_("西语展示名"))
    name_pt = models.CharField(max_length=80, blank=True, default='', verbose_name=_("葡语展示名"))
    sort = models.IntegerField(default=1, verbose_name=_("显示排序"))
    status = models.IntegerField(default=0, verbose_name=_("状态"))  # 1 启用
    vip = models.BooleanField(default=False, verbose_name=_("VIP 合集"))
    mode = models.CharField(max_length=16, default=MODE_AUTO, verbose_name=_("收录方式"))
    match = models.CharField(max_length=8, default=MATCH_ANY, verbose_name=_("条件关系"))
    conditions = models.JSONField(default=list, blank=True, verbose_name=_("自动条件"))
    manual_ids = models.JSONField(default=list, blank=True, verbose_name=_("手动内容ID"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_display_tag'
        verbose_name = _("前端展示标签")
        ordering = ['kind', 'sort', 'id']
        indexes = [
            models.Index(fields=['kind', 'status', 'sort'], name='display_tag_kind_idx'),
            models.Index(fields=['kind', 'display_type'], name='display_tag_type_idx'),
        ]


class DisplayStructureState(models.Model):
    """显性结构每 kind 一行：编辑脏标记、壁纸筛选草稿、已发布快照。"""

    KIND_QUOTE = DisplayTag.KIND_QUOTE
    KIND_WALLPAPER = DisplayTag.KIND_WALLPAPER

    kind = models.CharField(max_length=16, unique=True, verbose_name=_("结构种类"))
    dirty = models.BooleanField(default=False, verbose_name=_("有未发布调整"))
    published_version = models.IntegerField(default=0, verbose_name=_("已发布版本号"))
    wallpaper_filters = models.JSONField(default=dict, blank=True, verbose_name=_("壁纸筛选草稿"))
    wallpaper_filters_published = models.JSONField(default=dict, blank=True, verbose_name=_("壁纸筛选已发布"))
    published_snapshot = models.JSONField(default=dict, blank=True, verbose_name=_("已发布结构快照"))
    published_by = models.CharField(max_length=80, blank=True, default='', verbose_name=_("发布人"))
    published_at = models.DateTimeField(null=True, blank=True, verbose_name=_("发布时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_display_structure'
        verbose_name = _("显性结构状态")


class Example(models.Model):
    """示例表 — 建表规范参考"""

    STATUS_DRAFT = 0
    STATUS_ACTIVE = 1
    STATUS_DISABLED = 2
    STATUS_CHOICES = [
        (STATUS_DRAFT, '草稿'),
        (STATUS_ACTIVE, '启用'),
        (STATUS_DISABLED, '禁用'),
    ]

    name = models.CharField(max_length=100, verbose_name=_("名称"))
    description = models.TextField(null=True, blank=True, verbose_name=_("描述"))
    status = models.IntegerField(default=STATUS_ACTIVE, choices=STATUS_CHOICES, verbose_name=_("状态"))
    sort_order = models.IntegerField(default=0, verbose_name=_("排序"))
    is_deleted = models.BooleanField(default=False, verbose_name=_("是否删除"))
    remark = models.CharField(max_length=500, null=True, blank=True, verbose_name=_("备注"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        db_table = 't_example'
        verbose_name = _("示例")
        ordering = ['sort_order', '-created_at']

    def __str__(self):
        return self.name