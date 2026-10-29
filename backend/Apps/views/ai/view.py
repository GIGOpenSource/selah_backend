#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Selah AI：OpenAI Compatible 对话、额度扣次、安全预检与危机热线。

流水线（chat）：
1. can_consume_ai — 无额度返回 402（Free 可提示去打卡）。
2. 输入侧 safety_check — 命中则拒答、写审计、不扣次（stage=input）。
3. call_llm — 失败不扣次。
4. 输出侧 safety_check — 命中同样拒答不扣次（stage=output）。
5. 通过后 consume_ai_quota 再落助手消息（deducted=True）。

会话按用户本地自然日一容器（AiConversation）；历史默认取今日，可按 date 查往日。
"""
import json
import logging
import os
import urllib.error
import urllib.request

from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import (
    AiConversation, AiMessage, AiSafetyAudit, AiSafetyKeyword,
    CrisisHotline, AiQuickPrompt,
)
from Apps.views.ai.serializers import AiChatSerializer, AiDeleteMessageSerializer
from tools.permissions import IsTokenValid
from tools.selah_quota import (
    get_config, user_local_today, can_consume_ai, consume_ai_quota, get_remaining_quota,
    user_public_dict,
)
from tools.utils import ApiResponse

from tools.selah_enums import values_of, SAFETY_CATEGORIES

logger = logging.getLogger(__name__)

# 空库种子：类别键必须与 SAFETY_CATEGORIES 一致
SEED_KEYWORDS = {
    'self_harm': ['suicide', 'kill myself', 'end my life', 'self-harm', 'cut myself', 'want to die'],
    'violence': ['how to kill', 'make a bomb', 'terror attack'],
    'illegal': ['how to make drugs', 'buy a gun illegally'],
    'hate_extremism': ['holy war kill'],
    'religion_dispute': ['which denomination is the only true'],
    'medical': ['diagnose my depression', 'stop my medication'],
}

# 与 selah_enums.SAFETY_CATEGORIES 保持一致
assert set(SEED_KEYWORDS.keys()) == set(values_of(SAFETY_CATEGORIES))


def ensure_safety_seeds():
    """首次空库时写入默认安全词、热线与快捷开场。

    业务说明:
        仅当 AiSafetyKeyword 表为空才播种，避免覆盖运营已改数据。
        热线/快捷开场各自再判断空表，可独立补种。
    """
    if AiSafetyKeyword.objects.exists():
        return
    for cat, words in SEED_KEYWORDS.items():
        for w in words:
            AiSafetyKeyword.objects.create(category=cat, keyword=w, enabled=True)
    if not CrisisHotline.objects.exists():
        for row in [
            ('US', '988 Suicide & Crisis Lifeline', '988'),
            ('US', 'Emergency', '911'),
            ('GB', 'Samaritans', '116 123'),
            ('CA', 'Talk Suicide Canada', '1-833-456-4566'),
            ('AU', 'Lifeline', '13 11 14'),
        ]:
            CrisisHotline.objects.create(country=row[0], label=row[1], phone_or_url=row[2], enabled=True)
    if not AiQuickPrompt.objects.exists():
        for i, (t, p) in enumerate([
            ('Morning prayer', 'Help me start today with a short gentle prayer.'),
            ('Anxiety', 'I feel anxious. Offer a calm devotion and breathing prayer.'),
            ('Gratitude', 'Guide me to name three things I can thank God for today.'),
        ]):
            AiQuickPrompt.objects.create(title=t, prompt=p, sort_order=i, enabled=True)


def safety_check(text: str, user=None):
    """关键词安全预检；命中返回 (True, category)。

    Args:
        text: 待扫描文本（用户输入或模型输出）。
        user: 可选，用于按 app_key/system_lang/platform 作用域过滤关键词行。

    业务说明:
        - 大小写不敏感子串匹配。
        - 类别总开关：配置键 safety_cat_{category}_enabled，默认 True。
        - 先 ensure_safety_seeds，保证新环境有默认词表。
    """
    ensure_safety_seeds()
    from tools.selah_quota import filter_scoped_rows, normalize_app_key
    lower = (text or '').lower()
    lang = getattr(user, 'system_lang', '') if user else ''
    plat = getattr(user, 'platform', '') if user else ''
    app = getattr(user, 'app_key', '') if user else ''
    rows = filter_scoped_rows(
        list(AiSafetyKeyword.objects.filter(enabled=True)),
        lang, plat, app_key=normalize_app_key(app),
    )
    for row in rows:
        # 运营可按类别整体关闭（不必删词）
        cat_key = f'safety_cat_{row.category}_enabled'
        if not bool(get_config(cat_key, True)):
            continue
        if row.keyword.lower() in lower:
            return True, row.category
    return False, ''


def refuse_reply(category: str):
    """按类别取可配拒答文案；自残附危机尾注。

    业务说明:
        优先 ai_refuse_{category}，否则 ai_refuse_general。
        self_harm 额外拼接 ai_crisis_footer（若配置非空）。
    """
    key = f'ai_refuse_{category}' if category else 'ai_refuse_general'
    reply = get_config(key) or get_config('ai_refuse_general') or (
        'I care about your safety. I cannot help with that request.'
    )
    if category == 'self_harm':
        footer = get_config('ai_crisis_footer') or ''
        if footer:
            reply = f'{reply}\n\n{footer}'
    return reply


def call_llm(messages):
    """调用 LLM。

    Returns:
        (True, content) 成功；(False, error_str) 失败。

    业务说明:
        仅 provider=mock 走本地占位。未设 AI_API_KEY 时失败，不假装对话成功。
        正式调用 OpenAI Compatible /chat/completions。
    """
    provider = (get_config('ai_provider') or 'openai_compatible').lower()
    api_key = os.getenv('AI_API_KEY', '')
    # Gemini OpenAI 兼容端点；也可用 OpenRouter 等兼容网关
    api_base = os.getenv(
        'AI_API_BASE',
        'https://generativelanguage.googleapis.com/v1beta/openai',
    ).rstrip('/')
    # 环境变量优先，便于部署切换模型而不改运营台配置
    model = (os.getenv('AI_MODEL') or '').strip() or get_config('ai_model', 'gemini-3.8-flash')
    temperature = float(get_config('ai_temperature', 0.7))
    timeout = int(get_config('ai_timeout', 60))
    if provider == 'mock':
        last = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
        return True, (
            f"(Mock) Peace be with you. I hear you: \"{last[:120]}\". "
            "Take a slow breath and rest in God's presence. — Selah"
        )
    if not api_key:
        return False, 'AI_API_KEY not configured'
    url = f'{api_base}/chat/completions'
    payload = {
        'model': model,
        'temperature': temperature,
        'messages': messages,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode('utf-8'))
            content = body['choices'][0]['message']['content']
            return True, content
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = (e.read() or b'').decode('utf-8', errors='replace')[:800]
        except Exception:
            detail = ''
        logger.error(
            'LLM call failed: %s url=%s model=%s body=%s',
            e, url, model, detail or '-',
            exc_info=True,
        )
        return False, f'{e}; model={model}; {detail or "no body"}'
    except Exception as e:
        logger.error('LLM call failed: %s url=%s model=%s', e, url, model, exc_info=True)
        return False, str(e)


@extend_schema(tags=[_('Selah AI')])
class AiViewSet(ViewSet):
    """AI 对话：快捷开场、按日历史、聊天（含安全拒答）、删消息。"""

    permission_classes = [IsTokenValid]

    def get_throttle_scope(self):
        if getattr(self, 'action', None) in ('chat', 'send'):
            return 'chat'
        return 'user'

    def _conv(self, user):
        """取或创建用户当日本地会话。

        业务说明:
            一天一条 AiConversation，跨日自然新开；消息挂 conversation_id。
        """
        today = user_local_today(user)
        conv, _ = AiConversation.objects.get_or_create(user_id=user.id, local_date=today)
        return conv

    @extend_schema(summary=_('快捷开场'))
    @action(detail=False, methods=['get'], url_path='quick-prompts')
    def quick_prompts(self, request):
        """启用中的快捷开场列表（按 app×语言×平台作用域）。"""
        ensure_safety_seeds()
        from tools.selah_quota import filter_scoped_rows, normalize_app_key
        lang = getattr(request.user, 'system_lang', '') or ''
        plat = getattr(request.user, 'platform', '') or ''
        app = getattr(request.user, 'app_key', '') or ''
        rows = filter_scoped_rows(
            list(AiQuickPrompt.objects.filter(enabled=True).order_by('sort_order', 'id')),
            lang, plat, app_key=normalize_app_key(app),
        )
        return ApiResponse(data=[{'id': r.id, 'title': r.title, 'prompt': r.prompt} for r in rows])

    @extend_schema(summary=_('对话历史（可按日）'))
    @action(detail=False, methods=['get'], url_path='history')
    def history(self, request):
        """按日或今日会话历史与额度。

        Args:
            date: 可选 ISO 日期；缺省=今日本地会话（无则 get_or_create）。

        Returns:
            dates: 近 60 个有会话的本地日，供日历切换。
            quota: 当前剩余额度快照。
        """
        date_str = (request.query_params.get('date') or '').strip()
        if date_str:
            from datetime import date as date_cls
            try:
                local_date = date_cls.fromisoformat(date_str)
            except ValueError:
                return ApiResponse(message='invalid date', code=400)
            # 指定日：不自动建会话，无记录则 conversation_id=None
            conv = AiConversation.objects.filter(user_id=request.user.id, local_date=local_date).first()
        else:
            conv = self._conv(request.user)
        dates = list(
            AiConversation.objects.filter(user_id=request.user.id)
            .order_by('-local_date')
            .values_list('local_date', flat=True)[:60]
        )
        msgs = []
        if conv:
            msgs = AiMessage.objects.filter(conversation_id=conv.id).order_by('id')
        return ApiResponse(data={
            'conversation_id': conv.id if conv else None,
            'local_date': (conv.local_date.isoformat() if conv else None),
            'dates': [d.isoformat() for d in dates],
            'quota': get_remaining_quota(request.user),
            'messages': [
                {
                    'id': m.id,
                    'role': m.role,
                    'content': m.content,
                    'safety_blocked': m.safety_blocked,
                    'safety_category': m.safety_category,
                }
                for m in msgs
            ],
        })

    @extend_schema(summary=_('发送消息'))
    @action(detail=False, methods=['post'], url_path='chat')
    def chat(self, request):
        """发送消息：额度校验→安全预检→LLM→输出扫描→扣次。

        业务说明:
            - 402：额度不足；ledger=free_quota 时 can_checkin 提示可打卡领次。
            - 输入/输出安全命中均 HTTP 200 + code=ai_safety_blocked，便于客户端统一弹安全 UI；
              deducted=False；自残类附带热线（用户国 / US / GLOBAL）。
            - 上下文最多回溯 20 条非安全拒答消息 + system prompt。
        """
        ensure_safety_seeds()
        ser = AiChatSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        text = ser.validated_data['content'].strip()
        if not text:
            return ApiResponse(message='content required', code=400)

        # 先校验额度再落库，避免刷空消息占会话
        ok, info = can_consume_ai(request.user)
        if not ok:
            return ApiResponse(
                code=402,
                message=_('额度不足'),
                data={
                    'quota': info,
                    'pay_trigger_source': 'ai_quota',
                    # Free 账本耗尽时可引导打卡领次
                    'can_checkin': info.get('ledger') == 'free_quota',
                },
            )

        blocked, category = safety_check(text, request.user)
        conv = self._conv(request.user)
        # 用户消息始终落库（含被拒内容），便于审计与会话连贯
        AiMessage.objects.create(
            conversation_id=conv.id, user_id=request.user.id,
            role='user', content=text, deducted=False,
        )
        if blocked:
            AiSafetyAudit.objects.create(user_id=request.user.id, safety_category=category)
            hotlines = []
            if category == 'self_harm':
                # 自残：回传危机热线（用户国优先，并含 US/GLOBAL 兜底）
                from tools.selah_quota import filter_scoped_rows, normalize_app_key
                lang = getattr(request.user, 'system_lang', '') or ''
                plat = getattr(request.user, 'platform', '') or ''
                app = getattr(request.user, 'app_key', '') or ''
                hl_rows = filter_scoped_rows(
                    list(CrisisHotline.objects.filter(
                        enabled=True,
                        country__in=[request.user.register_country or 'US', 'US', 'GLOBAL'],
                    )),
                    lang, plat, app_key=normalize_app_key(app),
                )
                hotlines = [
                    {'country': r.country, 'label': r.label, 'phone_or_url': r.phone_or_url}
                    for r in hl_rows[:5]
                ]
            reply = refuse_reply(category)
            msg = AiMessage.objects.create(
                conversation_id=conv.id, user_id=request.user.id,
                role='assistant', content=reply, deducted=False,
                safety_blocked=True, safety_category=category,
            )
            return ApiResponse(
                code=200,
                message='ai_safety_blocked',
                data={
                    'code': 'ai_safety_blocked',
                    'safety_category': category,
                    'reply': reply,
                    'message_id': msg.id,
                    'hotlines': hotlines,
                    'quota': get_remaining_quota(request.user),
                    'deducted': False,
                    'stage': 'input',
                },
            )

        system = get_config('ai_system_prompt')
        # 最近 20 条（倒序取再 reverse），排除安全拒答以免污染上下文
        history = list(
            AiMessage.objects.filter(conversation_id=conv.id).order_by('-id')[:20]
        )
        history.reverse()
        messages = [{'role': 'system', 'content': system}]
        for m in history:
            if m.role in ('user', 'assistant') and not m.safety_blocked:
                messages.append({'role': m.role, 'content': m.content})

        success, result = call_llm(messages)
        if not success:
            # LLM 失败不扣次，客户端可重试
            return ApiResponse(
                code=500,
                message=_('AI 失败'),
                data={'deducted': False, 'quota': get_remaining_quota(request.user), 'error': result},
            )

        # 输出侧安全扫描：命中则拒答且不扣次
        out_blocked, out_cat = safety_check(result, request.user)
        if out_blocked:
            AiSafetyAudit.objects.create(user_id=request.user.id, safety_category=out_cat or 'output')
            hotlines = []
            if out_cat == 'self_harm':
                from tools.selah_quota import filter_scoped_rows, normalize_app_key
                lang = getattr(request.user, 'system_lang', '') or ''
                plat = getattr(request.user, 'platform', '') or ''
                app = getattr(request.user, 'app_key', '') or ''
                hl_rows = filter_scoped_rows(
                    list(CrisisHotline.objects.filter(
                        enabled=True,
                        country__in=[request.user.register_country or 'US', 'US', 'GLOBAL'],
                    )),
                    lang, plat, app_key=normalize_app_key(app),
                )
                hotlines = [
                    {'country': r.country, 'label': r.label, 'phone_or_url': r.phone_or_url}
                    for r in hl_rows[:5]
                ]
            reply = refuse_reply(out_cat)
            # 不落原始模型输出，只落拒答文案，避免客户端展示违规内容
            msg = AiMessage.objects.create(
                conversation_id=conv.id, user_id=request.user.id,
                role='assistant', content=reply, deducted=False,
                safety_blocked=True, safety_category=out_cat,
            )
            return ApiResponse(
                code=200,
                message='ai_safety_blocked',
                data={
                    'code': 'ai_safety_blocked',
                    'safety_category': out_cat,
                    'reply': reply,
                    'message_id': msg.id,
                    'hotlines': hotlines,
                    'quota': get_remaining_quota(request.user),
                    'deducted': False,
                    'stage': 'output',
                },
            )

        # 通过安全扫描后才扣次，再落助手消息
        consumed, qinfo = consume_ai_quota(request.user)
        msg = AiMessage.objects.create(
            conversation_id=conv.id, user_id=request.user.id,
            role='assistant', content=result, deducted=consumed,
        )
        return ApiResponse(data={
            'reply': result,
            'message_id': msg.id,
            'conversation_id': conv.id,
            'deducted': consumed,
            'quota': qinfo,
            'user': user_public_dict(request.user),
        })

    @extend_schema(summary=_('删除消息'))
    @action(detail=False, methods=['post'], url_path='delete-message')
    def delete_message(self, request):
        """删除本人消息。

        业务说明:
            按 id+user_id 删除，防越权；已扣次消息删除不退还额度（1.0 简化）。
        """
        ser = AiDeleteMessageSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        mid = ser.validated_data['id']
        AiMessage.objects.filter(id=mid, user_id=request.user.id).delete()
        return ApiResponse(message='ok')
