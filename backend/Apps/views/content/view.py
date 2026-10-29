#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah App 内容域 API：Feed/互动、主题壁纸、收藏夹、自我肯定、公开配置与冷启引导。

业务边界：
- Feed/互动面向 App 端登录用户（默认 IsTokenValid）；app_config 免登录供冷启/法律页拉取。
- VIP 锁定统一用 user.is_vip_active（等级+未过期），不单独查订单表。
- 新人喜欢任务按「累计喜欢条数」达标，取消喜欢不回退已完成标记。
"""
import logging
import random

from django.db.models import Max
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import (
    Quote, Topic, Wallpaper, UserContentHistory, UserLike, UserDislike,
    CollectionFolder, CollectionItem, Affirmation, AffirmationTemplate, WidgetCard, TopicFollow,
    FolderFollow, AffirmationFollow, WallpaperUsage, ContentReport, OnboardingQuestion, User,
    WelcomeScreen, CrisisHotline, InterstitialScreen, AppConfig, AppIcon, Quiz, QuizQuestion,
)

from Apps.views.analytics.view import record_content_open
from Apps.views.content.serializers import (
    QuoteSerializer, TopicSerializer, WallpaperSerializer, quote_card, affirmation_card,
    ContentIdSerializer, LikeSerializer, DislikeSerializer, CollectSerializer, ReportSerializer,
    FollowTopicSerializer, FollowFolderSerializer, FollowAllAffirmationsSerializer,
    WallpaperIdSerializer, FolderSaveSerializer,
    IdSerializer, AffirmationSaveSerializer, WidgetPrefsSerializer, SelectAppIconSerializer,
    AlarmPrefsSerializer, PrayerLockPrefsSerializer,
)
from tools.base_views import BaseViewSet
from tools.permissions import IsTokenValid, IsAdmin
from tools.selah_quota import (
    get_config, user_public_dict, filter_scoped_rows, normalize_lang, normalize_platform,
    normalize_app_key, set_config_context_from_request, get_config_context, DEFAULT_APP_KEY,
)
from tools.selah_recommend import build_feed, content_scope_q
from tools.selah_enums import (
    content_enums_payload,
    normalize_faith, validate_membership,
    FAITH_TYPES, PRAYER_FREQ, GENDERS, AGE_RANGES,
    ONBOARDING_KEY_BASIC_INFO, ONBOARDING_KEY_BASIC_INFO_LEGACY,
    ONBOARDING_COLUMN_KEYS, all_onboarding_key_values, interstitial_kind_values,
    as_options, ensure_profile_answers_2_0,
    default_folder_name, is_default_folder_name,
)
from tools.interest_theme import normalize_interest_list, is_valid_interest_value
from tools.utils import ApiResponse, CustomPagination
from Apps.views.analytics.view import record_user_event
from tools.display_structure import published_rows
from tools.tag_library import item_matches_display
from tools.pool_rank import bump_engage
logger = logging.getLogger(__name__)


def _scoped_quote(user, cid, request=None):
    """上架语录且在用户三维可见范围内；否则 None。"""
    return (
        Quote.objects.filter(id=int(cid), is_deleted=False, status=1)
        .filter(content_scope_q(user, match_language=False, request=request))
        .first()
    )


def _scoped_topic(user, tid, request=None):
    """上架主题且在用户三维可见范围内；否则 None。"""
    return (
        Topic.objects.filter(id=int(tid), is_deleted=False, status=1)
        .filter(content_scope_q(user, match_language=True, request=request))
        .first()
    )


def _scoped_wallpaper(user, wid, request=None):
    """上架壁纸且在用户三维可见范围内；否则 None。"""
    return (
        Wallpaper.objects.filter(id=int(wid), is_deleted=False, status=1)
        .filter(content_scope_q(user, match_language=True, request=request))
        .first()
    )


def _require_scoped_quote(user, cid, request=None):
    """校验语录可见；不可见返回 (None, 404 Response)。"""
    q = _scoped_quote(user, cid, request=request)
    if not q:
        return None, ApiResponse(message='not found', code=404)
    return q, None


def _affirmations_followed_all(uid):
    """本人全部自我肯定是否均已关注；无肯定语时为 False。"""
    aff_ids = set(Affirmation.objects.filter(user_id=uid).values_list('id', flat=True))
    if not aff_ids:
        return False
    followed = set(
        AffirmationFollow.objects.filter(
            user_id=uid, affirmation_id__in=aff_ids,
        ).values_list('affirmation_id', flat=True)
    )
    return followed == aff_ids


def _pick_followed_affirmations(uid, limit):
    """随机抽取本人已关注自我肯定，最多 limit 条。"""
    if limit <= 0:
        return []
    followed_ids = list(
        AffirmationFollow.objects.filter(user_id=uid).values_list('affirmation_id', flat=True)
    )
    if not followed_ids:
        return []
    k = min(limit, len(followed_ids))
    pick_ids = random.sample(followed_ids, k)
    aff_map = {
        a.id: a
        for a in Affirmation.objects.filter(user_id=uid, id__in=pick_ids)
    }
    return [aff_map[i] for i in pick_ids if i in aff_map]


_catalog_ready = False


def _ensure_catalog_once():
    """空库懒加载虚拟运营内容；进程内只跑一次成功路径。"""
    global _catalog_ready
    if _catalog_ready:
        return
    try:
        if not Quote.objects.filter(is_deleted=False, status=1).exists():
            from tools.selah_virtual_seed import ensure_virtual_catalog
            ensure_virtual_catalog(force_log=False)
        elif not Topic.objects.filter(is_deleted=False, status=1).exists():
            from tools.selah_virtual_seed import ensure_virtual_catalog
            ensure_virtual_catalog(force_log=False)
        elif not Wallpaper.objects.filter(is_deleted=False, status=1).exists():
            from tools.selah_virtual_seed import ensure_virtual_catalog
            ensure_virtual_catalog(force_log=False)
        _catalog_ready = True
    except Exception:
        logger.exception('virtual catalog ensure failed')


@extend_schema(tags=[_('Selah内容')])
class ContentViewSet(ViewSet):
    """首页内容与用户内容资产：Feed、喜欢/收藏/举报、主题壁纸、自我肯定、公开配置。

    权限：除 app_config 免鉴权、enums 需登录外，其余动作默认 IsTokenValid。
    """

    permission_classes_by_action = {
        'app_config': [],
        'welcome': [],
        'interstitial': [],
        'enums': [],
        'hotlines': [],
        'widget_cards': [],
    }

    def get_permissions(self):
        # 未登记的 action 一律要求登录态，避免新增接口漏配权限
        return [p() for p in self.permission_classes_by_action.get(self.action, [IsTokenValid])]

    @extend_schema(summary=_('首页 Feed'))
    @action(detail=False, methods=['get'], url_path='feed')
    def feed(self, request):
        """推荐语录 Feed（含喜欢态）；可混入本人已关注自我肯定。

        Args:
            pageSize: 可选，单页条数，服务端硬顶 30，防止一次拉空推荐池。

        Returns:
            list: 卡片列表；语录 kind=quote，自我肯定 kind=affirmation（仅本人）。

        业务说明:
            排序/去重/冷启比例由 build_feed 实现；已关注肯定语约占 15% 名额并随机打散。
            肯定语仅当前用户可见，不进入他人 Feed。
        """
        # 上限 30：保护推荐池与响应体积
        _ensure_catalog_once()
        size = min(int(request.query_params.get('pageSize') or 10), 30)
        uid = request.user.id

        aff_budget = 0
        if AffirmationFollow.objects.filter(user_id=uid).exists():
            aff_budget = max(1, int(round(size * 0.15)))
            aff_budget = min(aff_budget, size)
        aff_rows = _pick_followed_affirmations(uid, aff_budget)
        quote_size = max(0, size - len(aff_rows))
        quote_items = build_feed(request.user, page_size=quote_size, request=request) if quote_size else []

        quote_ids = [q.id for q in quote_items]
        aff_ids = [a.id for a in aff_rows]
        liked_quote_ids = set(
            UserLike.objects.filter(
                user_id=uid, content_id__in=quote_ids, target_type='quote',
            ).values_list('content_id', flat=True)
        ) if quote_ids else set()
        liked_aff_ids = set(
            UserLike.objects.filter(
                user_id=uid, content_id__in=aff_ids, target_type='affirmation',
            ).values_list('content_id', flat=True)
        ) if aff_ids else set()
        collected_aff_ids = set(
            CollectionItem.objects.filter(
                user_id=uid, content_id__in=aff_ids, target_type='affirmation',
            ).values_list('content_id', flat=True)
        ) if aff_ids else set()

        cards = (
            [quote_card(q, request.user, liked_quote_ids) for q in quote_items]
            + [affirmation_card(a, liked_aff_ids, collected_aff_ids) for a in aff_rows]
        )
        random.shuffle(cards)
        return ApiResponse(data={'list': cards})

    @extend_schema(summary=_('语录详情'))
    @action(detail=False, methods=['get'], url_path='quote')
    def quote_detail(self, request):
        """语录详情卡片。

        Args:
            id / content_id: 语录主键，二者兼容（历史客户端字段不一致）。

        业务说明:
            仅返回未软删且 status=1、且在用户 app×platform 可见范围内的语录；
            VIP 语录仍返回正文，locked 由 quote_card 标记。
        """
        cid = request.query_params.get('id') or request.query_params.get('content_id')
        if not cid:
            return ApiResponse(message='id required', code=400)
        q, err = _require_scoped_quote(request.user, cid, request=request)
        if err:
            return err
        liked = UserLike.objects.filter(
            user_id=request.user.id, content_id=q.id, target_type='quote',
        ).exists()
        disliked = UserDislike.objects.filter(user_id=request.user.id, content_id=q.id).exists()
        card = quote_card(q, request.user, {q.id} if liked else set())
        card['disliked'] = disliked
        return ApiResponse(data=card)

    @extend_schema(summary=_('曝光上报'))
    @action(detail=False, methods=['post'], url_path='expose')
    def expose(self, request):
        """上报内容曝光，写入历史。

        业务说明:
            每次曝光追加一条 UserContentHistory（action=expose），供推荐去重与「我的历史」；
            允许同一语录多次曝光（不做去重），以反映真实曝光频次。
            语录须在当前三维可见，否则 404（防深链打穿）。
        """
        ser = ContentIdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cid = ser.validated_data['content_id']
        _, err = _require_scoped_quote(request.user, cid, request=request)
        if err:
            return err
        UserContentHistory.objects.create(user_id=request.user.id, content_id=cid, action='expose')
        return ApiResponse(message='ok')

    @extend_schema(summary=_('分享上报'))
    @action(detail=False, methods=['post'], url_path='share')
    def share(self, request):
        """上报语录分享，累加 share_count 并埋点。

        业务说明:
            - bump_engage 累加 QuoteDailyEngage.share_count，驱动同池排序得分；
            - 写一条 quote_share 埋点，供运营看板统计；
            - 语录须在当前三维可见，否则 404（防深链打穿）。
        """
        ser = ContentIdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cid = ser.validated_data['content_id']
        q, err = _require_scoped_quote(request.user, cid, request=request)
        if err:
            return err
        bump_engage(cid, share=1)
        try:
            record_user_event(request.user, 'quote_share', {
                'content_id': cid,
                'topic_id': getattr(q, 'topic_id', None),
            })
        except Exception:
            logger.warning('share analytics failed content_id=%s', cid, exc_info=True)
        return ApiResponse(message='ok')

    @extend_schema(summary=_('喜欢/取消'))
    @action(detail=False, methods=['post'], url_path='like')
    def like(self, request):
        """喜欢/取消喜欢，并更新新人喜欢任务进度。

        Args:
            content_id: 内容 ID（语录或肯定语）。
            target_type: quote|affirmation，默认 quote。
            liked: true=写入喜欢；false=删除喜欢行。

        Returns:
            liked: 本次请求意图态。
            newcomer_like: 任务进度；just_completed 仅在「本次请求刚达标」为 true。

        业务说明:
            - 进度只统计 target_type=quote 的 UserLike（肯定语喜欢不计新人任务）。
            - 取消喜欢会减少 current，但已完成标记不回退。
            - goal 来自配置 newcomer_like_goal，默认 5。
        """
        ser = LikeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cid = ser.validated_data['content_id']
        target_type = ser.validated_data.get('target_type') or 'quote'
        liked = ser.validated_data.get('liked', True)
        uid = request.user.id

        if liked:
            if target_type == 'affirmation':
                ok = Affirmation.objects.filter(id=cid, user_id=uid).exists()
                if not ok:
                    return ApiResponse(message='affirmation not found', code=404)
            else:
                _, err = _require_scoped_quote(request.user, cid, request=request)
                if err:
                    return err
            UserLike.objects.get_or_create(
                user_id=uid, content_id=cid, target_type=target_type,
            )
        else:
            UserLike.objects.filter(
                user_id=uid, content_id=cid, target_type=target_type,
            ).delete()

        # 新人任务只数语录喜欢
        count = UserLike.objects.filter(user_id=uid, target_type='quote').count()
        goal = int(get_config('newcomer_like_goal', 5))
        just_completed = False
        user = request.user
        if not user.newcomer_like_completed and count >= goal:
            user.newcomer_like_completed = True
            user.save(update_fields=['newcomer_like_completed', 'updated_at'])
            just_completed = True
        return ApiResponse(data={
            'liked': bool(liked),
            'newcomer_like': {
                'current': min(count, goal) if not user.newcomer_like_completed else goal,
                'goal': goal,
                'completed': user.newcomer_like_completed,
                'just_completed': just_completed,
                'visible': not user.newcomer_like_completed or just_completed,
            },
        })

    @extend_schema(summary=_('新人喜欢任务进度'))
    @action(detail=False, methods=['get'], url_path='newcomer-like')
    def newcomer_like(self, request):
        """查询新人喜欢任务进度。

        业务说明:
            若库内标记未完成但累计喜欢已达 goal（如历史数据），GET 时补写完成标记，保证与 like 接口一致。
            visible=未完成时才展示入口。
        """
        uid = request.user.id
        count = UserLike.objects.filter(user_id=uid, target_type='quote').count()
        goal = int(get_config('newcomer_like_goal', 5))
        completed = request.user.newcomer_like_completed or count >= goal
        # 数据自愈：进度已达但标记未写时补齐
        if completed and not request.user.newcomer_like_completed:
            request.user.newcomer_like_completed = True
            request.user.save(update_fields=['newcomer_like_completed', 'updated_at'])
        return ApiResponse(data={
            'current': min(count, goal),
            'goal': goal,
            'completed': completed,
            'visible': not completed,
        })

    @extend_schema(summary=_('不喜欢'))
    @action(detail=False, methods=['post'], url_path='dislike')
    def dislike(self, request):
        """记录或不喜欢，供推荐负反馈与已屏蔽列表。

        Args:
            content_id: 语录 ID。
            disliked: true=写入屏蔽（默认）；false=取消屏蔽（删除 UserDislike）。

        业务说明:
            推荐 build_feed 会排除仍存在的 UserDislike。
            本接口不删除喜欢记录。
        """
        ser = DislikeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cid = ser.validated_data['content_id']
        disliked = ser.validated_data.get('disliked', True)
        _, err = _require_scoped_quote(request.user, cid, request=request)
        if err:
            return err
        if disliked:
            UserDislike.objects.get_or_create(user_id=request.user.id, content_id=cid)
        else:
            UserDislike.objects.filter(user_id=request.user.id, content_id=cid).delete()
        return ApiResponse(data={'disliked': bool(disliked)})

    @extend_schema(summary=_('收藏'))
    @action(detail=False, methods=['post'], url_path='collect')
    def collect(self, request):
        """收藏到指定夹（默认 Favorites）。

        业务说明:
            - target_type: quote|affirmation，默认 quote。
            - folder_id 缺省时取/建用户 is_default 夹（名 Favorites）。
            - update_or_create：同一 (user, target_type, content) 只属一夹。
        """
        ser = CollectSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cid = ser.validated_data['content_id']
        target_type = ser.validated_data.get('target_type') or 'quote'
        uid = request.user.id

        if target_type == 'affirmation':
            ok = Affirmation.objects.filter(id=cid, user_id=uid).exists()
            if not ok:
                return ApiResponse(message='affirmation not found', code=404)
        else:
            _, err = _require_scoped_quote(request.user, cid, request=request)
            if err:
                return err

        folder_id = ser.validated_data.get('folder_id')
        if not folder_id:
            folder, _ = CollectionFolder.objects.get_or_create(
                user_id=uid, is_default=True,
                defaults={'name': default_folder_name(normalize_lang(User.objects.values_list('system_lang', flat=True).get(id=request.user.id)))},
            )
            folder_id = folder.id

        CollectionItem.objects.update_or_create(
            user_id=uid,
            content_id=cid,
            target_type=target_type,
            defaults={'folder_id': folder_id},
        )
        return ApiResponse(data={'folder_id': folder_id})

    @extend_schema(summary=_('取消收藏'))
    @action(detail=False, methods=['post'], url_path='uncollect')
    def uncollect(self, request):
        """取消收藏。

        业务说明:
            按 user + target_type + content 删除 CollectionItem，不关心原属哪一夹。
        """
        ser = ContentIdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        target_type = ser.validated_data.get('target_type') or 'quote'
        CollectionItem.objects.filter(
            user_id=request.user.id,
            content_id=ser.validated_data['content_id'],
            target_type=target_type,
        ).delete()
        return ApiResponse(message='ok')

    @extend_schema(summary=_('主题列表'))
    @action(detail=False, methods=['get'], url_path='topics')
    def topics(self, request):
        """主题分页列表（含关注/锁定态）。

        业务说明:
            locked = 主题标 VIP 且用户当前无有效会员；仍返回主题元数据，客户端用 locked 做付费墙。
            按 app×language×platform 可见性过滤。
            followed_only=1：仅返回用户已关注主题（我的页）。
        """
        _ensure_catalog_once()
        qs = Topic.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', '-id')
        qs = qs.filter(content_scope_q(request.user, match_language=True, request=request))
        followed = set(
            TopicFollow.objects.filter(user_id=request.user.id).values_list('topic_id', flat=True)
        )
        # 我的页：仅已关注（使用即关注写入 TopicFollow）
        followed_only = str(request.query_params.get('followed_only') or '').strip() in ('1', 'true', 'True')
        if followed_only:
            if not followed:
                paginator = CustomPagination()
                page = paginator.paginate_queryset(qs.none(), request)
                return paginator.get_paginated_response([])
            qs = qs.filter(id__in=followed)
        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        data = []
        for t in page:
            item = TopicSerializer(t).data
            item['followed'] = t.id in followed
            # VIP 主题未开通：列表可见但进入详情会被 403
            item['locked'] = t.is_vip and not request.user.is_vip_active
            data.append(item)
        return paginator.get_paginated_response(data)

    @extend_schema(summary=_('主题详情语录'))
    @action(detail=False, methods=['get'], url_path='topic-quotes')
    def topic_quotes(self, request):
        """主题下语录分页；VIP 主题未开通返回锁定。

        业务说明:
            VIP 且未开通时 403+locked，不返回语录列表。
            followed 只读 TopicFollow；进详情不再自动关注。
        """
        tid = int(request.query_params.get('topic_id'))
        topic = _scoped_topic(request.user, tid, request=request)
        if not topic:
            return ApiResponse(message='not found', code=404)

        followed = TopicFollow.objects.filter(
            user_id=request.user.id, topic_id=tid,
        ).exists()
        locked = topic.is_vip and not request.user.is_vip_active

        # 付费墙：详情禁止拉正文；仍返回 followed 供按钮展示
        if locked:
            topic_data = TopicSerializer(topic).data
            topic_data['followed'] = followed
            topic_data['locked'] = True
            return ApiResponse(
                code=403,
                message=_('会员锁定'),
                data={
                    'locked': True,
                    'followed': followed,
                    'topic': topic_data,
                },
            )

        qs = Quote.objects.filter(topic_id=tid, is_deleted=False, status=1).order_by('-sort_weight', '-id')
        qs = qs.filter(content_scope_q(request.user, match_language=False, request=request))
        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        topic_data = TopicSerializer(topic).data
        topic_data['followed'] = followed
        topic_data['locked'] = False
        return ApiResponse(
            data={
                'topic': topic_data,
                'followed': followed,
                'list': [quote_card(q, request.user) for q in page],
            },
            pagination={
                'page': paginator.page.number,
                'page_size': paginator.page.paginator.per_page,
                'total': paginator.page.paginator.count,
                'total_pages': paginator.page.paginator.num_pages,
            },
        )

    @extend_schema(summary=_('关注主题'))
    @action(detail=False, methods=['post'], url_path='follow-topic')
    def follow_topic(self, request):
        """关注或取消关注主题。

        Args:
            follow: true=关注（幂等 get_or_create）；false=取消。

        业务说明:
            关注前校验主题在当前三维可见；取消关注不校验（允许清理历史关注）。
        """
        ser = FollowTopicSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        tid = ser.validated_data['topic_id']
        follow = ser.validated_data.get('follow', True)
        if follow:
            if not _scoped_topic(request.user, tid, request=request):
                return ApiResponse(message='not found', code=404)
            TopicFollow.objects.get_or_create(user_id=request.user.id, topic_id=tid)
        else:
            TopicFollow.objects.filter(user_id=request.user.id, topic_id=tid).delete()
        return ApiResponse(data={'followed': bool(follow)})

    @extend_schema(summary=_('关注收藏夹'))
    @action(detail=False, methods=['post'], url_path='follow-folder')
    def follow_folder(self, request):
        """关注或取消关注本人收藏夹。

        Args:
            folder_id: 收藏夹 ID（须属于当前用户）
            follow: true=关注（幂等）；false=取消。

        业务说明:
            仅本人夹；不做公开夹/关注他人。
        """
        ser = FollowFolderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        fid = ser.validated_data['folder_id']
        follow = ser.validated_data.get('follow', True)
        uid = request.user.id

        folder = CollectionFolder.objects.filter(id=fid, user_id=uid).first()
        if not folder:
            return ApiResponse(message='not found', code=404)

        if follow:
            FolderFollow.objects.get_or_create(user_id=uid, folder_id=fid)
        else:
            FolderFollow.objects.filter(user_id=uid, folder_id=fid).delete()

        return ApiResponse(data={
            'followed': bool(follow),
            'folder_id': folder.id,
            'folder': {
                'id': folder.id,
                'name': folder.name,
                'is_default': folder.is_default,
            },
        })

    @extend_schema(summary=_('清空关注主题'))
    @action(detail=False, methods=['post'], url_path='clear-followed-topics')
    def clear_followed_topics(self, request):
        """清空当前用户全部主题关注（我的页一键清空）。"""
        deleted, _ = TopicFollow.objects.filter(user_id=request.user.id).delete()
        return ApiResponse(data={'deleted': deleted}, message='ok')

    @extend_schema(summary=_('壁纸列表'))
    @action(detail=False, methods=['get'], url_path='wallpapers')
    def wallpapers(self, request):
        """壁纸分页列表（含 VIP 锁定态）。

        业务说明:
            locked 仅影响「设置」能力；列表仍返回图片 URL，客户端可预览模糊/水印策略自定。
            按 app×language×platform 可见性过滤。
            used_only=1：仅 WallpaperUsage 曾用记录（我的页；清空后为空；当前壁纸仍由 wallpaper_id 驱动首页）。
        """
        _ensure_catalog_once()
        qs = Wallpaper.objects.filter(is_deleted=False, status=1)
        qs = qs.filter(content_scope_q(request.user, match_language=True, request=request))
        media_type = (request.query_params.get('media_type') or '').strip().lower()
        if media_type in ('static', 'video'):
            qs = qs.filter(media_type=media_type)
        used_only = str(request.query_params.get('used_only') or '').strip() in ('1', 'true', 'True')
        if used_only:
            uid = request.user.id
            cur = request.user.wallpaper_id
            usage_ids = list(
                WallpaperUsage.objects.filter(user_id=uid)
                .order_by('-last_used_at', '-id')
                .values_list('wallpaper_id', flat=True)
            )
            if not usage_ids:
                paginator = CustomPagination()
                page = paginator.paginate_queryset(qs.none(), request)
                return paginator.get_paginated_response([])
            qs = qs.filter(id__in=usage_ids)
            id_order = {wid: i for i, wid in enumerate(usage_ids)}
            rows = list(qs)
            rows.sort(key=lambda w: id_order.get(w.id, 10**9))
            paginator = CustomPagination()
            page = paginator.paginate_queryset(rows, request)
            data = []
            for w in page:
                item = WallpaperSerializer(w).data
                item['locked'] = w.is_vip and not request.user.is_vip_active
                item['is_current'] = bool(cur and w.id == cur)
                data.append(item)
            return paginator.get_paginated_response(data)

        qs = qs.order_by('-sort_weight', '-id')
        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        data = []
        for w in page:
            item = WallpaperSerializer(w).data
            item['locked'] = w.is_vip and not request.user.is_vip_active
            data.append(item)
        return paginator.get_paginated_response(data)

    @extend_schema(summary=_('设置壁纸'))
    @action(detail=False, methods=['post'], url_path='set-wallpaper')
    def set_wallpaper(self, request):
        """设置当前壁纸（付费壁纸需 VIP）。

        业务说明:
            付费壁纸未开通返回 403，并带 pay_trigger_source=wallpaper_lock 供客户端归因开通入口。
            成功后写 User.wallpaper_id，并 upsert WallpaperUsage。
        """
        from django.utils import timezone

        ser = WallpaperIdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        wid = ser.validated_data['wallpaper_id']
        w = _scoped_wallpaper(request.user, wid, request=request)
        if not w:
            return ApiResponse(message='not found', code=404)
        # 付费墙 + 归因源，便于埋点/会员页跳转
        if w.is_vip and not request.user.is_vip_active:
            return ApiResponse(code=403, message=_('付费壁纸'), data={'pay_trigger_source': 'wallpaper_lock'})
        u = request.user
        u.wallpaper_id = wid
        u.save(update_fields=['wallpaper_id', 'updated_at'])
        usage, created = WallpaperUsage.objects.get_or_create(
            user_id=u.id, wallpaper_id=wid,
        )
        if not created:
            usage.last_used_at = timezone.now()
            usage.save(update_fields=['last_used_at'])
        record_content_open(
            u, 'wallpaper', wid,
            title=getattr(w, 'name_en', '') or getattr(w, 'theme', '') or '',
            app_key=getattr(u, 'app_key', '') or '',
            action='set',
        )
        return ApiResponse(data={'wallpaper_id': wid})

    @extend_schema(summary=_('清空曾用壁纸'))
    @action(detail=False, methods=['post'], url_path='clear-wallpaper-usage')
    def clear_wallpaper_usage(self, request):
        """清空壁纸使用历史；不改 User.wallpaper_id（当前壁纸保留）。"""
        deleted, _ = WallpaperUsage.objects.filter(user_id=request.user.id).delete()
        return ApiResponse(data={'deleted': deleted}, message='ok')
    @extend_schema(summary=_('前端展示标签'))
    @action(detail=False, methods=['get'], url_path='display-tags')
    def display_tags(self, request):
        """App 筛选入口。kind=quote|wallpaper；读已发布显性结构快照。"""
        kind = (request.query_params.get('kind') or 'quote').strip()
        if kind not in ('quote', 'wallpaper'):
            return ApiResponse(message='kind must be quote or wallpaper', code=400)
        from tools.display_structure import published_rows, serialize_wallpaper_filters
        if kind == 'wallpaper':
            data = [
                item for item in serialize_wallpaper_filters(published=True)
                if item.get('frontend_enabled')
            ]
            return ApiResponse(data=data)
        skip = {'column', 'filter'}
        data = [
            row for row in published_rows('quote')
            if row.get('status') in ('published', 1) and row.get('display_type') not in skip
        ]
        return ApiResponse(data=data)

    @extend_schema(summary=_('收藏夹'))
    @action(detail=False, methods=['get'], url_path='folders')
    def folders(self, request):
        """列出收藏夹；无则创建默认夹。

        业务说明:
            空列表时懒建 Favorites 默认夹，保证收藏接口与删除夹逻辑有落点。
            每项含 followed；followed_only=1 仅返回已关注夹。
        """
        uid = request.user.id
        folders = list(CollectionFolder.objects.filter(user_id=uid))
        if not folders:
            f = CollectionFolder.objects.create(
                user_id=uid,
                name=default_folder_name(normalize_lang(User.objects.values_list('system_lang', flat=True).get(id=request.user.id))),
                is_default=True,
            )
            folders = [f]

        # 显示层语言覆盖：默认夹且名字仍是已知默认值时，按当前用户语言返回
        cur_default_name = default_folder_name(normalize_lang(User.objects.values_list('system_lang', flat=True).get(id=request.user.id)))

        followed_ids = set(
            FolderFollow.objects.filter(user_id=uid).values_list('folder_id', flat=True)
        )
        followed_only = str(request.query_params.get('followed_only') or '').strip() in (
            '1', 'true', 'True',
        )
        if followed_only:
            folders = [f for f in folders if f.id in followed_ids]

        return ApiResponse(data=[{
            'id': f.id,
            'name': (cur_default_name if f.is_default and is_default_folder_name(f.name) else f.name),
            'is_default': f.is_default,
            'followed': f.id in followed_ids,
        } for f in folders])

    @extend_schema(summary=_('收藏夹 CRUD'))
    @action(detail=False, methods=['post'], url_path='folder-save')
    def folder_save(self, request):
        """新建或重命名收藏夹。

        业务说明:
            有 id 则仅改名（限本用户）；无 id 则新建非默认夹。
        """
        ser = FolderSaveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        uid = request.user.id
        fid = ser.validated_data.get('id')
        name = ser.validated_data['name']
        if fid:
            # 带 user_id 防越权改他人夹
            CollectionFolder.objects.filter(id=fid, user_id=uid).update(name=name)
            return ApiResponse(data={'id': fid, 'name': name})
        f = CollectionFolder.objects.create(user_id=uid, name=name)
        return ApiResponse(data={'id': f.id, 'name': f.name}, code=201)

    @extend_schema(summary=_('删除收藏夹'))
    @action(detail=False, methods=['post'], url_path='folder-delete')
    def folder_delete(self, request):
        """删除非默认夹，条目迁入默认夹。

        业务说明:
            先把该夹条目全部迁到默认夹，再删非默认夹；默认夹本身不可删（is_default=False 过滤）。
            无默认夹时先创建 Favorites。
        """
        ser = IdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        uid = request.user.id
        fid = ser.validated_data['id']
        folder = CollectionFolder.objects.filter(id=fid, user_id=uid).first()
        if not folder:
            return ApiResponse(message='not found', code=404)
        if folder.is_default:
            return ApiResponse(message='默认夹不可以删除', code=400)
        default = CollectionFolder.objects.filter(user_id=uid, is_default=True).first()
        if not default:
            default = CollectionFolder.objects.create(
                user_id=uid,
                name=default_folder_name(normalize_lang(User.objects.values_list('system_lang', flat=True).get(id=request.user.id))),
                is_default=True,
            )
        # 先迁移再删，避免收藏条目悬空
        CollectionItem.objects.filter(user_id=uid, folder_id=fid).update(folder_id=default.id)
        FolderFollow.objects.filter(user_id=uid, folder_id=fid).delete()
        CollectionFolder.objects.filter(id=fid, user_id=uid, is_default=False).delete()
        return ApiResponse(message='ok')

    @extend_schema(summary=_('我的喜欢/收藏/历史'))
    @action(detail=False, methods=['get'], url_path='my-assets')
    def my_assets(self, request):
        """我的喜欢/收藏/历史分页列表。

        Args:
            kind: like | collect | history（其它值走 history 分支）。
            folder_id: kind=collect 时可选，按夹过滤。
            display_tag_id: 可选；like/collect 时按语录展示标签 id 过滤（来自 display-tags）。

        业务说明:
            like/collect 可混排语录与肯定语，用 item_type 区分。
            history 仍只含语录曝光。
            已删内容会被过滤，不返回空壳。
        """
        kind = request.query_params.get('kind') or 'like'
        uid = request.user.id
        sort = (request.query_params.get('sort') or 'newest').strip().lower()
        if kind == 'like':
            qs = UserLike.objects.filter(user_id=uid)
            rel_qs = qs.order_by('created_at', 'id') if sort == 'oldest' else qs.order_by('-created_at', '-id')
        elif kind == 'collect':
            qs = CollectionItem.objects.filter(user_id=uid)
            folder_id = request.query_params.get('folder_id')
            if folder_id:
                qs = qs.filter(folder_id=int(folder_id))
            rel_qs = qs.order_by('created_at', 'id') if sort == 'oldest' else qs.order_by('-created_at', '-id')
        else:
            history_qs = (
                UserContentHistory.objects.filter(user_id=uid)
                .values('content_id')
                .annotate(latest_id=Max('id'), latest_at=Max('created_at'))
            )
            rel_qs = history_qs.order_by('latest_id') if sort == 'oldest' else history_qs.order_by('-latest_id')
        # 按展示标签筛选喜欢/收藏；不传 display_tag_id = 全部
        tag_raw = (request.query_params.get('display_tag_id') or '').strip()
        if tag_raw and kind in ('like', 'collect'):
            try:
                tag_id = int(tag_raw)
            except (TypeError, ValueError):
                return ApiResponse(message='display_tag_id invalid', code=400)

            from types import SimpleNamespace
            from tools.display_structure import published_rows
            from tools.tag_library import item_matches_display

            row_dict = next(
                (
                    r for r in published_rows('quote')
                    if int(r.get('id') or 0) == tag_id
                       and (r.get('display_type') or '') not in ('column', 'filter')
                       and r.get('status') in ('published', 1)
                ),
                None,
            )
            if not row_dict:
                return ApiResponse(message='display tag not found', code=404)

            rule = SimpleNamespace(
                mode=row_dict.get('mode') or 'auto',
                match=row_dict.get('match') or 'any',
                conditions=row_dict.get('conditions') or [],
                manual_ids=row_dict.get('manual_ids') or [],
            )

            quote_cids = list(
                rel_qs.filter(target_type='quote').values_list('content_id', flat=True)
            )
            matched_ids = set()
            if quote_cids:
                for q in Quote.objects.filter(
                        id__in=quote_cids, is_deleted=False, status=1,
                ):
                    if item_matches_display('quote', q, rule):
                        matched_ids.add(q.id)
            rel_qs = rel_qs.filter(
                target_type='quote', content_id__in=matched_ids,
            )
        paginator = CustomPagination()
        page = paginator.paginate_queryset(rel_qs, request)

        def _day(dt):
            if dt in (None, ''):
                return ''
            date_fn = getattr(dt, 'date', None)
            if callable(date_fn):
                try:
                    return date_fn().isoformat()
                except Exception:
                    pass
            iso = getattr(dt, 'isoformat', None)
            if callable(iso):
                try:
                    return str(iso())[:10]
                except Exception:
                    pass
            s = str(dt).strip()
            return s[:10] if len(s) >= 10 else s

        # —— history：仍只查语录 ——
        if kind not in ('like', 'collect'):
            ids = [row['content_id'] for row in page]
            date_by_cid = {row['content_id']: row.get('latest_at') for row in page}
            quotes = {
                q.id: q
                for q in Quote.objects.filter(id__in=ids, is_deleted=False, status=1).filter(
                    content_scope_q(request.user, match_language=False, request=request)
                )
            }
            liked_ids = set(
                UserLike.objects.filter(
                    user_id=uid, content_id__in=ids, target_type='quote',
                ).values_list('content_id', flat=True)
            )
            collected_ids = set(
                CollectionItem.objects.filter(
                    user_id=uid, content_id__in=ids, target_type='quote',
                ).values_list('content_id', flat=True)
            )
            cards = []
            for i in ids:
                if i not in quotes:
                    continue
                card = quote_card(quotes[i], request.user, liked_ids, collected_ids)
                card['item_type'] = 'quote'
                day = _day(date_by_cid.get(i))
                card['action_at'] = day
                card['created_at'] = day
                card['viewed_at'] = day
                cards.append(card)
            return paginator.get_paginated_response(cards)

        # —— like / collect：按 target_type 分别解析 ——
        quote_ids = []
        aff_ids = []
        for row in page:
            tt = getattr(row, 'target_type', None) or 'quote'
            if tt == 'affirmation':
                aff_ids.append(row.content_id)
            else:
                quote_ids.append(row.content_id)

        quotes = {
            q.id: q
            for q in Quote.objects.filter(id__in=quote_ids, is_deleted=False, status=1).filter(
                content_scope_q(request.user, match_language=False, request=request)
            )
        }
        affs = {
            a.id: a
            for a in Affirmation.objects.filter(id__in=aff_ids, user_id=uid)
        }

        liked_quote_ids = set(
            UserLike.objects.filter(
                user_id=uid, content_id__in=quote_ids, target_type='quote',
            ).values_list('content_id', flat=True)
        )
        liked_aff_ids = set(
            UserLike.objects.filter(
                user_id=uid, content_id__in=aff_ids, target_type='affirmation',
            ).values_list('content_id', flat=True)
        )
        collected_quote_ids = set(
            CollectionItem.objects.filter(
                user_id=uid, content_id__in=quote_ids, target_type='quote',
            ).values_list('content_id', flat=True)
        )
        collected_aff_ids = set(
            CollectionItem.objects.filter(
                user_id=uid, content_id__in=aff_ids, target_type='affirmation',
            ).values_list('content_id', flat=True)
        )

        cards = []
        for row in page:
            tt = getattr(row, 'target_type', None) or 'quote'
            cid = row.content_id
            day = _day(getattr(row, 'created_at', None))

            if tt == 'affirmation':
                a = affs.get(cid)
                if not a:
                    continue
                card = {
                    'id': a.id,
                    'content': a.content,
                    'item_type': 'affirmation',
                    'liked': a.id in liked_aff_ids,
                    'collected': a.id in collected_aff_ids,
                    'language': '',
                    'source_ref': '',
                    'faith_tags': [],
                    'interest_tags': [],
                    'is_vip': False,
                    'locked': False,
                    'audio_url': '',
                    'in_cold_start_pool': False,
                    'topic_id': getattr(a, 'theme_id', None),
                    'theme_id': getattr(a, 'theme_id', None),
                    'wallpaper_id': getattr(a, 'wallpaper_id', None),
                    'meta': getattr(a, 'meta', None) or {},
                }
            else:
                q = quotes.get(cid)
                if not q:
                    continue
                card = quote_card(q, request.user, liked_quote_ids, collected_quote_ids)
                card['item_type'] = 'quote'

            card['action_at'] = day
            card['created_at'] = day
            if kind == 'like':
                card['liked_at'] = day
            else:
                card['collected_at'] = day
                card['folder_id'] = getattr(row, 'folder_id', None)
            cards.append(card)

        return paginator.get_paginated_response(cards)

    @extend_schema(summary=_('自我肯定列表'))
    @action(detail=False, methods=['get'], url_path='affirmations')
    def affirmations(self, request):
        """自我肯定分页列表。

        Args:
            sort: newest (默认) / oldest / az — 按用户创建顺序或字母排序。

        业务说明:
            仅本人数据；带 liked / collected。
            results 为对象：followed 整体关注态 + list 分页条目。
        """
        uid = request.user.id
        sort = (request.query_params.get('sort') or 'newest').strip().lower()
        qs = Affirmation.objects.filter(user_id=uid)
        if sort == 'oldest':
            qs = qs.order_by('created_at', 'id')
        elif sort == 'az':
            qs = qs.order_by('content', 'created_at', 'id')
        else:  # newest
            qs = qs.order_by('-created_at', '-id')
        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        ids = [r.id for r in page]
        liked_ids = set(
            UserLike.objects.filter(
                user_id=uid, content_id__in=ids, target_type='affirmation',
            ).values_list('content_id', flat=True)
        )
        collected_ids = set(
            CollectionItem.objects.filter(
                user_id=uid, content_id__in=ids, target_type='affirmation',
            ).values_list('content_id', flat=True)
        )
        return paginator.get_paginated_response({
            'followed': _affirmations_followed_all(uid),
            'list': [{
                'id': r.id,
                'content': r.content,
                'theme_id': getattr(r, 'theme_id', None),
                'wallpaper_id': getattr(r, 'wallpaper_id', None),
                'meta': getattr(r, 'meta', None) or {},
                'liked': r.id in liked_ids,
                'collected': r.id in collected_ids,
                'created_at': r.created_at.date().isoformat() if r.created_at else '',
                'updated_at': r.updated_at.isoformat() if r.updated_at else None,
            } for r in page],
        })

    @extend_schema(summary=_('一键关注/取消自我肯定'))
    @action(detail=False, methods=['post'], url_path='follow-all-affirmations')
    def follow_all_affirmations(self, request):
        """一键关注或取消本人全部自我肯定。

        Args:
            follow: true=全部关注（幂等）；false=全部取消。默认 true。

        业务说明:
            关注后可混入本人 Feed；仅本人可见。无单条关注接口。
        """
        ser = FollowAllAffirmationsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        follow = ser.validated_data.get('follow', True)
        uid = request.user.id
        if follow:
            ids = list(Affirmation.objects.filter(user_id=uid).values_list('id', flat=True))
            existing = set(
                AffirmationFollow.objects.filter(
                    user_id=uid, affirmation_id__in=ids,
                ).values_list('affirmation_id', flat=True)
            )
            to_create = [
                AffirmationFollow(user_id=uid, affirmation_id=i)
                for i in ids if i not in existing
            ]
            if to_create:
                AffirmationFollow.objects.bulk_create(to_create, ignore_conflicts=True)
            return ApiResponse(data={
                'followed': True,
                'total': len(ids),
                'created': len(to_create),
            })
        deleted, _ = AffirmationFollow.objects.filter(user_id=uid).delete()
        return ApiResponse(data={
            'followed': False,
            'deleted': deleted,
        })

    @extend_schema(summary=_('保存自我肯定'))
    @action(detail=False, methods=['post'], url_path='affirmation-save')
    def affirmation_save(self, request):
        """新建或更新自我肯定。

        业务说明:
            有 id 且属本人则更新；无 id 则新建。content 去首尾空白。
            3.0 Compose：可选 theme_id / wallpaper_id / meta。
            新建成功后自动关注（写入 AffirmationFollow）。
        """
        ser = AffirmationSaveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        content = ser.validated_data['content'].strip()
        aid = ser.validated_data.get('id')
        theme_id = ser.validated_data.get('theme_id')
        wallpaper_id = ser.validated_data.get('wallpaper_id')
        meta = ser.validated_data.get('meta')
        if meta is None:
            meta = {}
        fields = {
            'content': content,
            'theme_id': theme_id,
            'wallpaper_id': wallpaper_id,
            'meta': meta if isinstance(meta, dict) else {},
        }
        created = False
        if aid:
            updated = Affirmation.objects.filter(id=aid, user_id=request.user.id).update(**fields)
            if not updated:
                return ApiResponse(message='not found', code=404)
            row = Affirmation.objects.get(id=aid)
        else:
            row = Affirmation.objects.create(user_id=request.user.id, **fields)
            created = True
            AffirmationFollow.objects.get_or_create(
                user_id=request.user.id, affirmation_id=row.id,
            )
        payload = {
            'id': row.id,
            'content': row.content,
            'theme_id': row.theme_id,
            'wallpaper_id': row.wallpaper_id,
            'meta': row.meta or {},
            'followed': AffirmationFollow.objects.filter(
                user_id=request.user.id, affirmation_id=row.id,
            ).exists(),
        }
        return ApiResponse(data=payload, code=201 if created else 200)

    @extend_schema(summary=_('删除自我肯定'))
    @action(detail=False, methods=['post'], url_path='affirmation-delete')
    def affirmation_delete(self, request):
        """删除自我肯定（限本人），并级联删除喜欢/收藏/关注。"""
        ser = IdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        aid = ser.validated_data['id']
        uid = request.user.id
        deleted, _ = Affirmation.objects.filter(id=aid, user_id=uid).delete()
        if deleted:
            UserLike.objects.filter(
                user_id=uid, content_id=aid, target_type='affirmation',
            ).delete()
            CollectionItem.objects.filter(
                user_id=uid, content_id=aid, target_type='affirmation',
            ).delete()
            AffirmationFollow.objects.filter(user_id=uid, affirmation_id=aid).delete()
        return ApiResponse(message='ok')

    @extend_schema(summary=_('运营预置肯定语模板'))
    @action(detail=False, methods=['get'], url_path='affirmation-templates')
    def affirmation_templates(self, request):
        """启用中的肯定语模板（按 app×lang×platform 作用域挑选）。

        业务说明:
            供 Assets 空态一键填入；不代替用户自建肯定语。
        """
        user = request.user
        lang = normalize_lang(getattr(user, 'system_lang', None) or 'en')
        plat = normalize_platform(getattr(user, 'platform', None) or '')
        app = normalize_app_key(getattr(user, 'app_key', None) or '')
        rows = list(
            AffirmationTemplate.objects.filter(is_deleted=False, status=1).order_by('-sort_weight', 'id')
        )
        picked = filter_scoped_rows(rows, lang, plat, group_key=None, app_key=app)
        return ApiResponse(data=[
            {'id': r.id, 'content': r.content, 'sort_weight': r.sort_weight}
            for r in picked[:20]
        ])

    @extend_schema(summary=_('清空浏览历史'))
    @action(detail=False, methods=['post'], url_path='history-clear')
    def history_clear(self, request):
        """清空当前用户全部内容曝光历史。"""
        deleted, _ = UserContentHistory.objects.filter(user_id=request.user.id).delete()
        return ApiResponse(data={'deleted': deleted}, message='ok')

    @extend_schema(summary=_('内容枚举（App 对齐）'))
    @action(detail=False, methods=['get'], url_path='enums')
    def enums(self, request):
        """返回内容相关枚举供 App 对齐。

        业务说明:
            信仰/兴趣/举报原因等与运营台、引导题共用 selah_enums，避免前后端硬编码漂移。
            topic_categories 的 label 按用户语言本地化（TOPIC_CATEGORY_I18N）。
        """
        user = request.user
        lang = normalize_lang(
            request.query_params.get('language')
            or getattr(user, 'system_lang', None)
            or (request.headers.get('Accept-Language') or '').split(',')[0]
            or 'en'
        )
        return ApiResponse(data=content_enums_payload(lang=lang))

    @extend_schema(summary=_('公开配置'))
    @action(detail=False, methods=['get'], url_path='app-config')
    def app_config(self, request):
        """公开品牌/额度/IAP 等配置（免登录）。

        业务说明:
            - 仅白名单 keys，避免把内部密钥类配置暴露。
            - membership_products 失败时降级为空列表，不阻断冷启。
            - 法务字段空则保持空，不改写成占位主体或强制 /legal/。
            - 免登录：用 Header/Query 的 app_key×language×platform 写配置上下文。
        """
        from tools.selah_quota import get_configs, set_config_context_from_request, consume_config_degraded
        set_config_context_from_request(request, prefer_user=True)
        keys = [
            'brand_name', 'legal_entity_name', 'url_privacy', 'url_terms', 'url_help',
            'url_download', 'url_subscription', 'url_instagram', 'url_tiktok', 'url_facebook',
            'support_email', 'checkin_auto_popup', 'cold_start_interest_ratio',
            'cold_start_ops_ratio', 'free_quota_cap', 'vip_daily_quota', 'checkin_reward',
            'iap_ios_product_id', 'iap_android_product_id',
            'iap_ios_yearly_product_id', 'iap_android_yearly_product_id', 'trial_days',
            'vip_benefits', 'vip_cta_text', 'ai_greeting', 'streak_miss_reset_days',
            'welcome_enabled', 'newcomer_like_goal',
            'newcomer_intro_title', 'newcomer_intro_body',
            'newcomer_done_text', 'newcomer_congrats_text',
            'selah_2_0_features_enabled', 'trial_intro_enabled',
            'widget_teaser_enabled', 'app_icon_teaser_enabled',
            'dynamic_wallpaper_tab_enabled',
            # Selah 3.0
            'selah_3_0_features_enabled', 'compose_enabled', 'quiz_enabled',
            'support_selah_enabled', 'url_rate', 'compose_entry_label',
            'support_rate_label', 'support_share_label', 'support_help_label',
            'widget_enabled', 'app_icon_enabled', 'dynamic_wallpaper_enabled',
            'widget_default_theme', 'widget_default_border', 'widget_default_refresh',
            'widget_vip_borders', 'watch_enabled',
            # Manna
            'manna_features_enabled', 'manna_vip_enabled', 'versecard_brand',
            'share_text_template', 'stats_card_enabled', 'manna_newcomer_grant',
            # Welcome / Login 展示（按 app_key×语言×设备）
            'login_title', 'login_subtitle',
            'bible_listen_bg_url', 'translate_enabled',
            'manna_quiz_hub_bg_url', 'manna_quiz_hub_bg_night_url',
            'device_login_enabled', 'oauth_login_enabled',
            'iap_verify_enabled', 'iap_restore_enabled',
        ]
        data = get_configs(keys)
        data['config_degraded'] = consume_config_degraded()
        # 兼容：真能力开关回落到 teaser 键
        if 'widget_enabled' not in data or data.get('widget_enabled') is None:
            data['widget_enabled'] = bool(data.get('widget_teaser_enabled'))
        if 'app_icon_enabled' not in data or data.get('app_icon_enabled') is None:
            data['app_icon_enabled'] = bool(data.get('app_icon_teaser_enabled'))
        if 'dynamic_wallpaper_enabled' not in data or data.get('dynamic_wallpaper_enabled') is None:
            data['dynamic_wallpaper_enabled'] = bool(data.get('dynamic_wallpaper_tab_enabled'))
        # teaser 键与真能力对齐，避免 App 旧逻辑看不到能力
        data['widget_teaser_enabled'] = bool(data.get('widget_enabled', data.get('widget_teaser_enabled')))
        data['app_icon_teaser_enabled'] = bool(data.get('app_icon_enabled', data.get('app_icon_teaser_enabled')))
        data['dynamic_wallpaper_tab_enabled'] = bool(
            data.get('dynamic_wallpaper_enabled', data.get('dynamic_wallpaper_tab_enabled'))
        )
        try:
            from tools.membership_products import list_enabled_products_for_app
            data['membership_products'] = list_enabled_products_for_app()
        except Exception:
            # 商品表未就绪时不拖垮冷启配置
            data['membership_products'] = []
        # 包内快照比对用：白名单 key 的最新 updated_at（ISO）
        try:
            mx = (
                AppConfig.objects.filter(key__in=keys)
                .order_by('-updated_at')
                .values_list('updated_at', flat=True)
                .first()
            )
            data['revision'] = mx.isoformat() if mx else '0'
        except Exception:
            data['revision'] = '0'
        return ApiResponse(data=data)

    @extend_schema(summary=_('冷启 Welcome'))
    @action(detail=False, methods=['get'], url_path='welcome')
    def welcome(self, request):
        """拉取当前 app×语言×平台下启用的 Welcome 屏（免登录）。

        业务说明:
            welcome_enabled=false 或无启用行时 data=null，客户端跳过。
            同维度多行时取作用域分最高且 priority 更大的一条（同分再看 sort_order）。
            Terms/Privacy URL 仍由 app-config 提供。
        """
        from tools.selah_quota import (
            get_config, filter_scoped_rows, ensure_default_welcome_screens,
            normalize_lang, normalize_platform, normalize_app_key,
            set_config_context_from_request,
        )
        set_config_context_from_request(request, prefer_user=True)
        if not get_config('welcome_enabled', True):
            return ApiResponse(data=None)
        ensure_default_welcome_screens()
        from tools.selah_virtual_seed import ensure_virtual_welcome
        ensure_virtual_welcome()
        # 与 Header/Query 上下文一致（免登录靠 X-App-Key / Accept-Language / X-Platform）
        from tools.selah_quota import get_config_context
        app, lang, plat = get_config_context()
        # query 显式覆盖（含空串=强制全局维）已在 set_config_context_from_request 处理；
        # 此处再允许 language/platform/app_key 参数覆盖（调试预览）
        if request.query_params.get('language') is not None:
            lang = normalize_lang(request.query_params.get('language'))
        if request.query_params.get('platform') is not None:
            plat = normalize_platform(request.query_params.get('platform'))
        if request.query_params.get('app_key') is not None:
            app = normalize_app_key(request.query_params.get('app_key'))
        rows = list(WelcomeScreen.objects.filter(enabled=True).order_by('sort_order', 'id'))
        rows = filter_scoped_rows(rows, lang, plat, group_key=None, app_key=app)
        if not rows:
            return ApiResponse(data=None)
        r = rows[0]
        return ApiResponse(data={
            'id': r.id,
            'title': r.title,
            'subtitle': r.subtitle or '',
            'cta_text': r.cta_text or 'Continue',
            'image_url': r.image_url or '',
            'version': int(r.version or 1),
        })

    @extend_schema(summary=_('教育夹层'))
    @action(detail=False, methods=['get'], url_path='interstitial')
    def interstitial(self, request):
        """按 kind 拉取启用夹层（免登录；仿 welcome + kind 过滤）。

        Query:
            kind: 必填；未知 kind → 400。
        """
        from tools.selah_quota import (
            filter_scoped_rows, normalize_lang, normalize_platform, normalize_app_key,
            set_config_context_from_request, get_config_context,
        )
        set_config_context_from_request(request, prefer_user=True)
        kind = (request.query_params.get('kind') or '').strip()
        if not kind or kind not in interstitial_kind_values():
            return ApiResponse(message=_('invalid kind'), code=400)
        app, lang, plat = get_config_context()
        if request.query_params.get('language') is not None:
            lang = normalize_lang(request.query_params.get('language'))
        if request.query_params.get('platform') is not None:
            plat = normalize_platform(request.query_params.get('platform'))
        if request.query_params.get('app_key') is not None:
            app = normalize_app_key(request.query_params.get('app_key'))
        rows = list(
            InterstitialScreen.objects.filter(enabled=True, kind=kind).order_by('sort_order', 'id')
        )
        if not rows:
            from tools.selah_virtual_seed import ensure_virtual_interstitials
            ensure_virtual_interstitials()
            rows = list(
                InterstitialScreen.objects.filter(enabled=True, kind=kind).order_by('sort_order', 'id')
            )
        rows = filter_scoped_rows(rows, lang, plat, group_key=None, app_key=app)
        if not rows:
            return ApiResponse(data=None)
        r = rows[0]
        return ApiResponse(data={
            'id': r.id,
            'kind': r.kind,
            'title': r.title,
            'subtitle': r.subtitle or '',
            'cta_text': r.cta_text or 'Continue',
            'image_url': r.image_url or '',
            'version': int(r.version or 1),
        })

    @extend_schema(summary=_('危机热线列表'))
    @action(detail=False, methods=['get'], url_path='hotlines')
    def hotlines(self, request):
        """公开/登录均可拉启用热线（Help 页与 AI 拒答共用数据源）。

        Query:
            country: 可选国家码；缺省返回全部启用行（含 GLOBAL）。
            按 app×language×platform 作用域回退过滤。
        """
        from tools.selah_quota import (
            set_config_context_from_request, filter_scoped_rows, get_config_context,
            normalize_lang, normalize_platform, normalize_app_key,
        )
        set_config_context_from_request(request, prefer_user=True)
        app, lang, plat = get_config_context()
        if request.query_params.get('language') is not None:
            lang = normalize_lang(request.query_params.get('language'))
        if request.query_params.get('platform') is not None:
            plat = normalize_platform(request.query_params.get('platform'))
        if request.query_params.get('app_key') is not None:
            app = normalize_app_key(request.query_params.get('app_key'))
        qs = CrisisHotline.objects.filter(enabled=True).order_by('id')
        country = (request.query_params.get('country') or '').strip().upper()
        if country:
            qs = qs.filter(country__in=[country, 'US', 'GLOBAL', 'global', 'Global'])
        rows = filter_scoped_rows(list(qs), lang, plat, app_key=app)
        data = [
            {
                'id': h.id,
                'country': h.country,
                'label': h.label,
                'phone_or_url': h.phone_or_url,
            }
            for h in rows
        ]
        return ApiResponse(data={'list': data})

    def _normalize_widget_prefs(self, user, incoming=None):
        from tools.selah_quota import get_config
        base = {
            'theme_key': get_config('widget_default_theme', 'default') or 'default',
            'border_key': get_config('widget_default_border', 'plain') or 'plain',
            'refresh': get_config('widget_default_refresh', 'daily') or 'daily',
            'quote_source': 'recommend',
            'followed_topic_ids': [],
            'show_buttons': False,
            'customize': True,
        }
        stored = getattr(user, 'widget_prefs', None) or {}
        if isinstance(stored, dict):
            base.update({k: v for k, v in stored.items() if v is not None})
        if isinstance(incoming, dict):
            base.update({k: v for k, v in incoming.items() if v is not None})
        vip_borders = get_config('widget_vip_borders', []) or []
        if not isinstance(vip_borders, list):
            vip_borders = []
        if base.get('border_key') in vip_borders and not user.is_vip_active:
            base['border_key'] = get_config('widget_default_border', 'plain') or 'plain'
        if not isinstance(base.get('followed_topic_ids'), list):
            base['followed_topic_ids'] = []
        return base

    @extend_schema(summary=_('小组件卡片列表'))
    @action(detail=False, methods=['get'], url_path='widget-cards')
    def widget_cards(self, request):
        """运营配置的「文字 + 描述 + 图片」卡片组；免登录。

        按 app×lang×platform 作用域过滤（X-App-Key / ?app_key= / 登录用户字段）；
        未带 app 时默认 selah。可选 ?limit=（默认 50，最大 100）。
        """
        set_config_context_from_request(request, prefer_user=True)
        app, lang, plat = get_config_context()
        if not app:
            app = DEFAULT_APP_KEY
        try:
            limit = int(request.query_params.get('limit') or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(100, limit))
        rows = list(
            WidgetCard.objects.filter(is_deleted=False, enabled=True).order_by('sort_order', 'id')
        )
        picked = filter_scoped_rows(rows, lang, plat, group_key=None, app_key=app)
        picked.sort(key=lambda r: (getattr(r, 'sort_order', 0) or 0, getattr(r, 'id', 0) or 0))
        return ApiResponse(data={
            'list': [
                {
                    'id': r.id,
                    'text': r.text,
                    'description': getattr(r, 'description', '') or '',
                    'image_url': r.image_url or '',
                    'image_type': getattr(r, 'image_type', None) or 'square',
                    'sort_order': r.sort_order,
                }
                for r in picked[:limit]
            ],
        })

    @extend_schema(summary=_('小组件偏好'))
    @action(detail=False, methods=['get', 'post'], url_path='widget-prefs')
    def widget_prefs(self, request):
        """GET 当前偏好；POST 合并更新（VIP 边框校验）。"""
        u = request.user
        if request.method.lower() == 'get':
            return ApiResponse(data=self._normalize_widget_prefs(u))
        ser = WidgetPrefsSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        prefs = self._normalize_widget_prefs(u, ser.validated_data)
        from tools.selah_quota import get_config
        vip_borders = get_config('widget_vip_borders', []) or []
        if prefs.get('border_key') in (vip_borders if isinstance(vip_borders, list) else []) and not u.is_vip_active:
            return ApiResponse(code=403, message=_('VIP border'), data={'pay_trigger_source': 'widget_border'})
        u.widget_prefs = prefs
        u.save(update_fields=['widget_prefs', 'updated_at'])
        return ApiResponse(data=prefs)

    def _normalize_alarm_prefs(self, user, incoming=None):
        base = {'enabled': False, 'hour': 8, 'minute': 0}
        stored = getattr(user, 'alarm_prefs', None) or {}
        if isinstance(stored, dict):
            base.update({k: v for k, v in stored.items() if v is not None})
        if isinstance(incoming, dict):
            base.update({k: v for k, v in incoming.items() if v is not None})
        try:
            base['hour'] = max(0, min(23, int(base.get('hour', 8))))
            base['minute'] = max(0, min(59, int(base.get('minute', 0))))
        except (TypeError, ValueError):
            base['hour'], base['minute'] = 8, 0
        base['enabled'] = bool(base.get('enabled'))
        return base

    def _normalize_prayer_lock_prefs(self, user, incoming=None):
        base = {'enabled': False, 'app_ids': [], 'last_prayer_date': ''}
        stored = getattr(user, 'prayer_lock_prefs', None) or {}
        if isinstance(stored, dict):
            base.update({k: v for k, v in stored.items() if v is not None})
        if isinstance(incoming, dict):
            base.update({k: v for k, v in incoming.items() if v is not None})
        base['enabled'] = bool(base.get('enabled'))
        ids = base.get('app_ids') or base.get('feature_ids') or []
        if not isinstance(ids, list):
            ids = []
        never = {'prayer_lock', 'alarm', 'settings', 'home', 'ai'}
        cleaned = []
        for x in ids:
            s = str(x).strip()[:64]
            if s and s not in never and s not in cleaned:
                cleaned.append(s)
        base['app_ids'] = cleaned[:40]
        base.pop('feature_ids', None)
        date_s = str(base.get('last_prayer_date') or '').strip()[:32]
        base['last_prayer_date'] = date_s
        return base

    @extend_schema(summary=_('祷告闹钟偏好'))
    @action(detail=False, methods=['get', 'post'], url_path='alarm-prefs')
    def alarm_prefs(self, request):
        """GET/POST 祷告闹钟偏好（enabled / hour / minute）。"""
        u = request.user
        if request.method.lower() == 'get':
            return ApiResponse(data=self._normalize_alarm_prefs(u))
        ser = AlarmPrefsSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        prefs = self._normalize_alarm_prefs(u, ser.validated_data)
        u.alarm_prefs = prefs
        u.save(update_fields=['alarm_prefs', 'updated_at'])
        return ApiResponse(data=prefs)

    @extend_schema(summary=_('祷告锁偏好'))
    @action(detail=False, methods=['get', 'post'], url_path='prayer-lock-prefs')
    def prayer_lock_prefs(self, request):
        """GET/POST App 内祷告锁：锁定功能列表 + last_prayer_date（本地日）。"""
        u = request.user
        if request.method.lower() == 'get':
            return ApiResponse(data=self._normalize_prayer_lock_prefs(u))
        ser = PrayerLockPrefsSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        prefs = self._normalize_prayer_lock_prefs(u, ser.validated_data)
        u.prayer_lock_prefs = prefs
        u.save(update_fields=['prayer_lock_prefs', 'updated_at'])
        return ApiResponse(data=prefs)

    @extend_schema(summary=_('小组件今日载荷'))
    @action(detail=False, methods=['get'], url_path='widget-payload')
    def widget_payload(self, request):
        """Extension / Watch 用：今日语录摘要 + 主题色 + 壁纸缩略图。"""
        prefs = self._normalize_widget_prefs(request.user)
        quote = None
        if prefs.get('quote_source') == 'followed':
            topic_ids = prefs.get('followed_topic_ids') or list(
                TopicFollow.objects.filter(user_id=request.user.id).values_list('topic_id', flat=True)[:20]
            )
            if topic_ids:
                quote = (
                    Quote.objects.filter(
                        is_deleted=False, status=1, topic_id__in=topic_ids,
                    )
                    .filter(content_scope_q(request.user, match_language=False, request=request))
                    .order_by('-id')
                    .first()
                )
        if not quote:
            try:
                cards = build_feed(request.user, page_size=1, request=request)
                if cards:
                    first = cards[0]
                    if isinstance(first, Quote):
                        quote = first
                    elif isinstance(first, dict) and first.get('id'):
                        quote = Quote.objects.filter(id=first['id']).first()
            except TypeError:
                cards = build_feed(request.user, 1)
                if cards:
                    first = cards[0]
                    quote = first if isinstance(first, Quote) else Quote.objects.filter(id=getattr(first, 'id', None) or (first.get('id') if isinstance(first, dict) else 0)).first()
        if not quote:
            return ApiResponse(data={
                'empty': True,
                'quote': None,
                'theme_key': prefs.get('theme_key'),
                'border_key': prefs.get('border_key'),
                'wallpaper_thumb': '',
            })
        wall = None
        if request.user.wallpaper_id:
            wall = Wallpaper.objects.filter(id=request.user.wallpaper_id, is_deleted=False).first()
        thumb = ''
        if wall:
            if getattr(wall, 'media_type', 'static') == 'video':
                thumb = wall.poster_url or wall.image_url or ''
            else:
                thumb = wall.image_url or ''
        return ApiResponse(data={
            'empty': False,
            'quote': {
                'id': quote.id,
                'content': quote.content,
                'source_ref': getattr(quote, 'source_ref', None) or '',
            },
            'theme_key': prefs.get('theme_key'),
            'border_key': prefs.get('border_key'),
            'refresh': prefs.get('refresh'),
            'wallpaper_thumb': thumb,
            'accent': '#B6BADD',
        })

    @extend_schema(summary=_('App 图标列表'))
    @action(detail=False, methods=['get'], url_path='app-icons')
    def app_icons(self, request):
        lang = normalize_lang(getattr(request.user, 'system_lang', None) or 'en')
        plat = normalize_platform(getattr(request.user, 'platform', None) or '')
        app = normalize_app_key(getattr(request.user, 'app_key', None) or '')
        rows = list(AppIcon.objects.filter(is_deleted=False, enabled=True).order_by('sort_order', 'id'))
        picked = filter_scoped_rows(rows, lang, plat, group_key=None, app_key=app)
        selected = getattr(request.user, 'selected_icon_key', '') or ''
        data = []
        for r in picked:
            locked = bool(r.is_vip) and not request.user.is_vip_active
            data.append({
                'icon_key': r.icon_key,
                'title': r.title,
                'preview_url': r.preview_url,
                'is_vip': r.is_vip,
                'locked': locked,
                'selected': r.icon_key == selected,
            })
        return ApiResponse(data=data)

    @extend_schema(summary=_('选择 App 图标'))
    @action(detail=False, methods=['post'], url_path='select-app-icon')
    def select_app_icon(self, request):
        ser = SelectAppIconSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        key = ser.validated_data['icon_key']
        row = AppIcon.objects.filter(icon_key=key, is_deleted=False, enabled=True).first()
        if not row:
            return ApiResponse(message='not found', code=404)
        if row.is_vip and not request.user.is_vip_active:
            return ApiResponse(code=403, message=_('VIP icon'), data={'pay_trigger_source': 'app_icon'})
        u = request.user
        u.selected_icon_key = key
        u.save(update_fields=['selected_icon_key', 'updated_at'])
        return ApiResponse(data={'selected_icon_key': key})


@extend_schema(tags=[_('Selah Onboarding')])
class OnboardingViewSet(ViewSet):
    """冷启问卷：拉题、完成引导写画像、跳过引导。

    业务说明:
        complete 会写信仰/兴趣等画像并置 onboarding_completed；
        skip 置跳过标记，并为 2.0 扩展题在 profile_answers 写入空占位（已有值不覆盖）。
    """

    permission_classes = [IsTokenValid]

    def _serialize_question(self, r):
        """题目序列化；基础信息题附带性别/年龄枚举。

        业务说明:
            旧 question_key（LEGACY）对外统一归一为 ONBOARDING_KEY_BASIC_INFO，
            并附 meta.genders / age_ranges 供客户端渲染复合题。
        """
        item = {
            'question_key': r.question_key,
            'title': r.title,
            'options': r.options or [],
            'sort_order': int(getattr(r, 'sort_order', 0) or 0),
        }
        key = r.question_key
        if key in (ONBOARDING_KEY_BASIC_INFO, ONBOARDING_KEY_BASIC_INFO_LEGACY):
            # 兼容历史题 key，对外只暴露新 key
            if key == ONBOARDING_KEY_BASIC_INFO_LEGACY:
                item['question_key'] = ONBOARDING_KEY_BASIC_INFO
            item['meta'] = {
                'genders': as_options(GENDERS),
                'age_ranges': as_options(AGE_RANGES),
            }
        return item

    @extend_schema(summary=_('题目'))
    @action(detail=False, methods=['get'], url_path='questions')
    def questions(self, request):
        """拉取启用中的引导题目（按 app×语言×平台作用域）。

        Args:
            language / platform / app_key: 可选覆盖用户画像。

        业务说明:
            filter_scoped_rows 按三维回退选中一套题；
            当前 App 无启用题时返回空列表（客户端跳过问卷，不回落硬编码默认集）。
        """
        from tools.selah_quota import filter_scoped_rows, normalize_app_key
        lang = getattr(request.user, 'system_lang', '') or ''
        plat = getattr(request.user, 'platform', '') or ''
        app = getattr(request.user, 'app_key', '') or ''
        # 查询参数显式覆盖（预览/调试用）
        if request.query_params.get('language') is not None:
            lang = request.query_params.get('language')
        if request.query_params.get('platform') is not None:
            plat = request.query_params.get('platform')
        if request.query_params.get('app_key') is not None:
            app = request.query_params.get('app_key')
        rows = list(OnboardingQuestion.objects.filter(enabled=True).order_by('sort_order', 'id'))
        # 同 question_key 多维度时只保留最匹配一条
        rows = filter_scoped_rows(
            rows, lang, plat, group_key='question_key', app_key=normalize_app_key(app),
        )
        if not rows:
            return ApiResponse(
                data=[],
                meta={'revision': '0'},
            )
        mx = (
            OnboardingQuestion.objects.filter(enabled=True)
            .order_by('-updated_at')
            .values_list('updated_at', flat=True)
            .first()
        )
        revision = mx.isoformat() if mx else '0'
        return ApiResponse(
            data=[self._serialize_question(r) for r in rows],
            meta={'revision': revision},
        )

    @extend_schema(summary=_('完成引导'))
    @action(detail=False, methods=['post'], url_path='complete')
    def complete(self, request):
        """提交引导答案并标记完成，返回公开用户信息。

        业务说明:
            支持扁平字段（1.0）与 answers{}（2.0 扩展题）。
            answers 内题库 key → profile_answers；1.0 列字段双写；未知 key 忽略。
            各画像字段均为「有值才校验并覆盖」；非法枚举直接 400。
            age_group 为 age_range 历史别名。
            完成后 onboarding_skipped=False，与 skip 互斥。
        """
        u = request.user
        known_keys = set(all_onboarding_key_values()) | ONBOARDING_COLUMN_KEYS | {
            ONBOARDING_KEY_BASIC_INFO, ONBOARDING_KEY_BASIC_INFO_LEGACY,
            'age_group',
        }
        answers = request.data.get('answers')
        flat = {}
        if isinstance(answers, dict):
            for k, v in answers.items():
                key = str(k).strip()
                if not key or key not in known_keys:
                    continue
                flat[key] = v
        for k in (
            'faith_type', 'prayer_frequency', 'interest_tags', 'display_name',
            'gender', 'age_range', 'age_group',
        ):
            if k in request.data and request.data.get(k) is not None:
                flat[k] = request.data.get(k)

        faith = normalize_faith(flat.get('faith_type') or '')
        if faith:
            ok, bad = validate_membership([faith], FAITH_TYPES, field_name='faith_type')
            if bad:
                return ApiResponse(message=f'invalid faith_type: {bad}', code=400)
            u.faith_type = ok[0] if ok else u.faith_type

        prayer_raw = flat.get('prayer_frequency')
        prayer = (str(prayer_raw).strip() if prayer_raw is not None else '')
        if prayer:
            ok, bad = validate_membership([prayer], PRAYER_FREQ)
            if bad:
                return ApiResponse(message=f'invalid prayer_frequency: {bad}', code=400)
            u.prayer_frequency = ok[0] if ok else u.prayer_frequency

        tags = flat.get('interest_tags')
        if isinstance(tags, list):
            bad = [str(t).strip() for t in tags if str(t).strip() and not is_valid_interest_value(t)]
            if bad:
                return ApiResponse(message=f'invalid interest_tags: {bad}', code=400)
            u.interest_tags = normalize_interest_list(tags)

        basic = flat.get(ONBOARDING_KEY_BASIC_INFO) or flat.get(ONBOARDING_KEY_BASIC_INFO_LEGACY)
        if isinstance(basic, dict):
            if basic.get('display_name') is not None:
                flat['display_name'] = basic.get('display_name')
            if basic.get('gender') is not None:
                flat['gender'] = basic.get('gender')
            if basic.get('age_range') is not None or basic.get('age_group') is not None:
                flat['age_range'] = basic.get('age_range') or basic.get('age_group')

        if flat.get('display_name') is not None:
            u.display_name = flat.get('display_name') or u.display_name

        gender = (str(flat.get('gender') or '')).strip()
        if gender:
            ok, bad = validate_membership([gender], GENDERS)
            if bad:
                return ApiResponse(message=f'invalid gender: {bad}', code=400)
            u.gender = ok[0]

        age = (str(flat.get('age_range') or flat.get('age_group') or '')).strip()
        if age:
            ok, bad = validate_membership([age], AGE_RANGES)
            if bad:
                return ApiResponse(message=f'invalid age_range: {bad}', code=400)
            u.age_range = ok[0]

        profile = dict(getattr(u, 'profile_answers', None) or {})
        skip_json = ONBOARDING_COLUMN_KEYS | {
            ONBOARDING_KEY_BASIC_INFO, ONBOARDING_KEY_BASIC_INFO_LEGACY, 'age_group',
        }
        allowed_json = set(all_onboarding_key_values())
        for key, val in flat.items():
            if key in skip_json or key not in allowed_json:
                continue
            profile[key] = val
        u.profile_answers = ensure_profile_answers_2_0(profile)

        u.onboarding_completed = True
        u.onboarding_skipped = False
        u.save()
        record_user_event(u, 'onboarding_complete', {'source': 'server'})
        return ApiResponse(data=user_public_dict(u), message=_('完成'))

    @extend_schema(summary=_('跳过引导'))
    @action(detail=False, methods=['post'], url_path='skip')
    def skip(self, request):
        """跳过引导：打标记，并为未答的 2.0 扩展题写入空占位。

        业务说明:
            置 onboarding_skipped=True、completed=False；
            信仰/兴趣等列字段保持原值；profile_answers 补齐 2.0 key（空串或空数组）。
        """
        u = request.user
        u.onboarding_skipped = True
        u.onboarding_completed = False
        u.profile_answers = ensure_profile_answers_2_0(getattr(u, 'profile_answers', None))
        u.save(update_fields=[
            'onboarding_skipped', 'onboarding_completed', 'profile_answers', 'updated_at',
        ])
        return ApiResponse(data=user_public_dict(u), message=_('已跳过'))
