#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""圣经正文与阅读资产 API。"""
import logging

from django.db.models import Count
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from models.models import (
    BibleAmen, BibleBook, BibleChapter, BibleHighlight, BibleNote,
    BibleTranslation, BibleVerse, MannaContentTag, MannaFavorite,
)

from tools.manna_biz import evaluate_manna_badges, grant_bible_read_reward, touch_activity_day, user_local_now
from tools.permissions import IsTokenValid
from tools.selah_quota import (
    MANNA_APP_KEY,
    config_as_bool,
    filter_scoped_rows,
    normalize_app_key,
    normalize_lang,
    normalize_platform,
    set_config_context,
    set_config_context_from_request,
)
from tools.utils import ApiResponse
from Apps.views.analytics.view import record_content_open
logger = logging.getLogger(__name__)

# Google Translate API 允许的语言码（内部用 xx → Google 用 xx-YY 映射）
_GT_ALLOWED = {
    'en': 'en', 'zh': 'zh-CN', 'zh-cn': 'zh-CN', 'zh-tw': 'zh-TW',
    'es': 'es', 'pt': 'pt', 'ja': 'ja', 'ko': 'ko',
}


# 英文动词/名词的简单复数/人称变化 + 常见圣经同义词
_EN_WORD_VARIANTS = {
    'doctor': ['doctors', 'physician', 'physicians', 'medical', 'healer', 'healers'],
    'physician': ['physicians', 'doctor', 'doctors'],
    'son': ['sons', 'child', 'children', 'offspring'],
    'child': ['children', 'son', 'sons', 'daughter', 'daughters'],
    'daughter': ['daughters', 'girls', 'maidens'],
    'king': ['kings', 'ruler', 'rulers', 'prince', 'princes'],
    'servant': ['servants', 'slave', 'slaves', 'steward', 'stewards', 'minister', 'ministers'],
    'father': ['fathers', 'ancestors', 'forefathers'],
    'mother': ['mothers', 'parents', 'nurse', 'nurses'],
    'brother': ['brothers', 'brethren', 'relatives'],
    'sister': ['sisters'],
    'priest': ['priests', 'minister', 'ministers', 'pastor', 'pastors', 'preacher', 'preachers'],
    'prophet': ['prophets', 'seer', 'seers', 'speaker'],
    'land': ['lands', 'earth', 'world', 'ground', 'territory', 'region'],
    'earth': ['land', 'world', 'ground', 'creation'],
    'city': ['cities', 'town', 'towns', 'village', 'villages'],
    'water': ['waters', 'river', 'rivers', 'sea', 'seas', 'flood', 'deluge'],
    'bread': ['loaves', 'food', 'meal', 'feast'],
    'food': ['bread', 'meat', 'feast', 'meal', 'provision'],
    'life': ['lives', 'soul', 'spirit', 'being'],
    'death': ['deaths', 'die', 'died', 'dying', 'slay', 'slain'],
    'sin': ['sins', 'sinful', 'wickedness', 'evil', 'transgression'],
    'law': ['laws', 'commandment', 'commandments', 'statute', 'statutes', 'ordinance'],
    'word': ['words', 'sayings', 'message', 'messages', 'speech', 'teaching'],
    'worship': ['praise', 'prayer', 'adoration', 'serve', 'serve', 'service'],
    'temple': ['tabernacle', 'sanctuary', 'church', 'house of god'],
    'church': ['congregation', 'assembly', 'tabernacle'],
    'sheep': ['flock', 'lamb', 'lambs'],
    'good': ['goodness', 'righteous', 'righteousness', 'virtue', 'blessed'],
    'evil': ['wicked', 'wickedness', 'bad', 'harmful', 'sinful', 'iniquity'],
    'love': ['loved', 'loves', 'loving', 'affection', 'charity'],
    'mercy': ['compassion', 'grace', 'kindness', 'pity'],
    'peace': ['rest', 'quietness', 'tranquility', 'shalom'],
    'hope': ['expectation', 'anticipation', 'trust', 'confidence'],
    'faith': ['belief', 'trust', 'confidence', 'assurance'],
    'grace': ['mercy', 'favor', 'kindness'],
    'prayer': ['prayers', 'supplication', 'petition', 'cry', 'cries'],
    'praise': ['worship', 'thanksgiving', 'adoration', 'glorify', 'glory'],
    'angel': ['angels', 'messenger', 'messengers'],
    'demon': ['demons', 'devil', 'devils', 'evil spirit'],
    'spirit': ['spirits', 'ghost', 'soul'],
    'holy': ['sacred', 'sanctified', 'blessed', 'pure'],
    'altar': ['altars', 'sacrifice', 'offerings'],
}


def _expand_english_terms(terms):
    """对翻译得到的英文候选词做扩展，加入常见同义词和复数形式，提升 Bible 检索召回率。"""
    expanded = list(terms)  # 先保留原词
    seen = {t.lower() for t in expanded}
    for t in terms:
        tl = str(t).lower()
        # 简单复数规则
        if not tl.endswith('s') and tl.isalpha():
            plural = tl + ('es' if tl.endswith(('s', 'x', 'ch', 'sh')) else 's')
            if plural not in seen:
                expanded.append(plural)
                seen.add(plural)
        # 去掉末尾 s 的单数形式
        if tl.endswith('ses') or tl.endswith('xes'):
            singular = tl[:-2]
            if singular not in seen:
                expanded.append(singular)
                seen.add(singular)
        elif tl.endswith('s') and len(tl) > 3 and not tl.endswith('ss'):
            singular = tl[:-1]
            if singular not in seen:
                expanded.append(singular)
                seen.add(singular)
        # 查同义词表
        for syn in _EN_WORD_VARIANTS.get(tl, []):
            if syn not in seen:
                expanded.append(syn)
                seen.add(syn)
    return expanded


def _google_translate(texts, target, source='auto', app='', lang='', plat=''):
    """调用 Google Translate 翻译一段文本列表，返回与输入等长的译文列表。

    - texts: List[str]
    - target: 内部语言码（如 'zh', 'en'），内部自动映射到 Google 语言码
    - source: 'auto' 或具体语言码
    - 失败时返回 [原文本, ...]（不抛异常，让上层决定如何处理）
    """
    import json
    import os
    import urllib.error
    import urllib.parse

    from django.conf import settings as dj_settings
    from tools.http_retry import http_get, http_json

    if not texts:
        return []

    api_key = (os.getenv('GOOGLE_TRANSLATE_API_KEY') or '').strip()
    tl = _GT_ALLOWED.get(str(target or 'en').lower(), 'en')
    sl = _GT_ALLOWED.get(str(source or 'auto').lower(), '') or 'auto'

    def _chunk_translate(chunk):
        if api_key:
            q = [str(x) for x in chunk]
            url = 'https://translation.googleapis.com/language/translate/v2?key=%s' % urllib.parse.quote(api_key)
            payload = {'q': q, 'target': tl, 'format': 'text'}
            if sl and sl != 'auto':
                payload['source'] = sl
            _, data = http_json(url, method='POST', data=payload, timeout=20, retries=2)
            rows = ((data or {}).get('data') or {}).get('translations') or []
            return [str(r.get('translatedText') or '') for r in rows]
        # gtx (无 key)
        q = '\n'.join(str(x or '') for x in chunk)
        params = urllib.parse.urlencode({'client': 'gtx', 'sl': sl, 'tl': tl, 'dt': 't', 'q': q})
        url = f'https://translate.googleapis.com/translate_a/single?{params}'
        ua = f'MannaBible/1.0 app={app};plat={plat};lang={lang}'
        _, raw = http_get(url, headers={'User-Agent': ua}, timeout=20, retries=2)
        data = json.loads(raw)
        parts = []
        for row in (data[0] or []):
            if row and row[0]:
                parts.append(row[0])
        return ''.join(parts).split('\n')

    results = [str(x or '') for x in texts]
    api_available = bool(api_key) or bool(getattr(dj_settings, 'DEBUG', False))
    if not api_available:
        return results  # 无可用翻译能力，直接返回原文

    out = []
    try:
        i = 0
        while i < len(results):
            chunk = results[i:i + 12]
            translated = _chunk_translate(chunk)
            if len(translated) != len(chunk):
                translated = []
                for one in chunk:
                    if not str(one).strip():
                        translated.append(one)
                        continue
                    one_out = _chunk_translate([one])
                    translated.append(one_out[0] if one_out else one)
            out.extend(translated)
            i += 12
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, OSError, KeyError, TypeError) as e:
        logger.warning('_google_translate failed target=%s: %s', tl, e)
        return results  # 翻译失败，退回原文

    while len(out) < len(results):
        out.append(results[len(out)])
    return out[:len(results)]


def _scope(user):
    app = normalize_app_key(getattr(user, 'app_key', '') or '') or MANNA_APP_KEY
    lang = normalize_lang(getattr(user, 'system_lang', None) or 'en')
    plat = normalize_platform(getattr(user, 'platform', None) or '')
    return app, lang, plat


@extend_schema(tags=[_('Bible')])
class BibleViewSet(ViewSet):
    permission_classes = [IsTokenValid]

    def get_throttle_scope(self):
        if getattr(self, 'action', None) == 'translate':
            return 'translate'
        return 'user'

    @extend_schema(summary=_('译本列表'))
    @action(detail=False, methods=['get'], url_path='translations')
    def translations(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        rows = filter_scoped_rows(
            list(BibleTranslation.objects.filter(enabled=True).order_by('sort_order', 'id')),
            lang, plat, group_key='code', app_key=app,
        )
        return ApiResponse(data=[{
            'id': r.id, 'code': r.code, 'title': r.title, 'language': r.language or '',
        } for r in rows])

    @extend_schema(summary=_('书卷目录'))
    @action(detail=False, methods=['get'], url_path='books')
    def books(self, request):
        set_config_context_from_request(request)
        code = (request.query_params.get('translation') or 'web').strip()
        app, lang, plat = _scope(request.user)
        tr = filter_scoped_rows(
            list(BibleTranslation.objects.filter(enabled=True, code=code)),
            lang, plat, group_key='code', app_key=app,
        )
        if not tr:
            tr_obj = BibleTranslation.objects.filter(enabled=True, code=code).first()
        else:
            tr_obj = tr[0]
        if not tr_obj:
            return ApiResponse(data=[])
        tag = (request.query_params.get('tag') or '').strip()
        qs = BibleBook.objects.filter(translation_id=tr_obj.id).order_by('order_index', 'id')
        counts = {
            row['book_id']: row['c']
            for row in BibleChapter.objects.filter(
                book_id__in=list(qs.values_list('id', flat=True)),
            ).values('book_id').annotate(c=Count('id'))
        }
        data = []
        for b in qs:
            tags = b.tag_keys if isinstance(b.tag_keys, list) else []
            if tag and tag not in tags:
                continue
            data.append({
                'id': b.id, 'book_key': b.book_key, 'name': b.name,
                'order_index': b.order_index, 'testament': b.testament,
                'tag_keys': tags, 'chapter_count': int(counts.get(b.id) or 0),
            })
        return ApiResponse(data=data)

    @extend_schema(summary=_('章节经文'))
    @action(detail=False, methods=['get'], url_path='chapter')
    def chapter(self, request):
        set_config_context_from_request(request)
        code = (request.query_params.get('translation') or 'web').strip()
        book_key = (request.query_params.get('book') or '').strip()
        try:
            chapter_num = int(request.query_params.get('chapter') or 0)
        except (TypeError, ValueError):
            chapter_num = 0
        if not book_key or chapter_num < 1:
            return ApiResponse(message='book/chapter required', code=400)
        app, lang, plat = _scope(request.user)
        tr_list = filter_scoped_rows(
            list(BibleTranslation.objects.filter(enabled=True, code=code)),
            lang, plat, group_key='code', app_key=app,
        )
        tr = tr_list[0] if tr_list else BibleTranslation.objects.filter(enabled=True, code=code).first()
        if not tr:
            return ApiResponse(message='translation not found', code=404)
        book = BibleBook.objects.filter(translation_id=tr.id, book_key=book_key).first()
        if not book:
            return ApiResponse(message='book not found', code=404)
        ch = BibleChapter.objects.filter(book_id=book.id, chapter_num=chapter_num).first()
        if not ch:
            return ApiResponse(message='chapter not found', code=404)
        verses = list(
            BibleVerse.objects.filter(chapter_id=ch.id).order_by('verse_num', 'id')
        )

        # 与 favorite/toggle 一致：可用 Header 覆盖 app
        headers = getattr(request, 'headers', None) or {}
        hdr_app = normalize_app_key(
            headers.get('X-App-Key') or headers.get('app-key') or ''
        )
        if hdr_app:
            app = hdr_app

        # 本章书签 key，须与前端 toggle 的 target_key 一致
        # 格式：{translation}:{book_key}:{chapter}:0
        # 例：web:genesis:1:0
        bookmark_key = '%s:%s:%s:0' % (tr.code, book.book_key, ch.chapter_num)
        bookmarked = MannaFavorite.objects.filter(
            user_id=request.user.id,
            app_key=app,
            target_type='bible_bookmark',
            target_key=bookmark_key,
        ).exists()

        content_id = '%s:%s:%s' % (tr.code, book.book_key, ch.chapter_num)
        record_content_open(
            request.user, 'bible_chapter', content_id,
            title='%s %s' % (book.name or book.book_key, ch.chapter_num),
            app_key=app,
            translation=tr.code,
            book_key=book.book_key,
            chapter=ch.chapter_num,
        )

        return ApiResponse(data={
            'translation': tr.code,
            'book_key': book.book_key,
            'book_name': book.name,
            'chapter': ch.chapter_num,
            'chapter_count': BibleChapter.objects.filter(book_id=book.id).count(),
            'audio_url': ch.audio_url or '',
            'audio_duration_sec': int(ch.audio_duration_sec or 0),
            'bookmarked': bookmarked,
            'verses': [{'verse': v.verse_num, 'text': v.text} for v in verses],
        })

    @extend_schema(summary=_('经文搜索'))
    @action(detail=False, methods=['get'], url_path='search')
    def search(self, request):
        set_config_context_from_request(request)
        q = (request.query_params.get('q') or '').strip()
        if len(q) < 2:
            return ApiResponse(message='q required', code=400)
        code = (request.query_params.get('translation') or 'web').strip()
        app, lang, plat = _scope(request.user)
        # Header 优先覆盖：与 translate 接口一致，避免 user.system_lang 过时
        headers = getattr(request, 'headers', None) or {}
        hdr_app = normalize_app_key(
            headers.get('X-App-Key') or headers.get('app-key') or ''
        )
        if hdr_app:
            app = hdr_app
        hdr_lang = normalize_lang(
            (headers.get('Accept-Language') or '').split(',')[0] or ''
        )
        if hdr_lang:
            lang = hdr_lang
        hdr_plat = normalize_platform(
            headers.get('X-Platform') or headers.get('platform') or ''
        )
        if hdr_plat:
            plat = hdr_plat
        tr_list = filter_scoped_rows(
            list(BibleTranslation.objects.filter(enabled=True, code=code)),
            lang, plat, group_key='code', app_key=app,
        )
        tr = tr_list[0] if tr_list else BibleTranslation.objects.filter(enabled=True, code=code).first()
        if not tr:
            return ApiResponse(data=[])
        book_ids = list(
            BibleBook.objects.filter(translation_id=tr.id).values_list('id', flat=True)
        )
        ch_ids = list(
            BibleChapter.objects.filter(book_id__in=book_ids).values_list('id', flat=True)
        )

        # 跨语言搜索：DB 只有英文原文，先把 query 翻成英文再搜
        search_terms = [q]
        # 如果用户语言不是英文，把 query 也翻成英文候选一起搜；英文用户直接用原文
        if lang and str(lang).lower() not in ('en',):
            en_candidates = _google_translate([q], target='en', source='auto',
                                              app=app, lang=lang, plat=plat)
            en_candidates = _expand_english_terms([str(x or '').strip() for x in en_candidates])
            for cand in en_candidates:
                cand = str(cand or '').strip()
                if cand and cand.lower() not in (t.lower() for t in search_terms):
                    search_terms.append(cand)

        from django.db.models import Q
        qs = BibleVerse.objects.filter(chapter_id__in=ch_ids)
        term_qs = Q(text__icontains=search_terms[0])
        for term in search_terms[1:]:
            term_qs |= Q(text__icontains=term)
        verse_rows = list(qs.filter(term_qs).order_by('id')[:40])

        used_ch = {v.chapter_id for v in verse_rows}
        chapters = {
            c.id: c for c in BibleChapter.objects.filter(id__in=used_ch)
        }
        used_books = {c.book_id for c in chapters.values()}
        books = {
            b.id: b for b in BibleBook.objects.filter(id__in=used_books)
        }
        out = []
        # 把搜到的 verse.text（英文）批量翻译成用户 system_lang
        user_lang_code = _GT_ALLOWED.get(str(lang or 'en').lower(), 'en')
        if verse_rows and user_lang_code != 'en':
            texts_to_translate = [str(v.text or '') for v in verse_rows]
            translated = _google_translate(texts_to_translate, target=user_lang_code,
                                           source='en', app=app, lang=lang, plat=plat)
        else:
            translated = None
        for idx, v in enumerate(verse_rows):
            ch = chapters.get(v.chapter_id)
            book = books.get(ch.book_id) if ch else None
            if not ch or not book:
                continue
            final_text = translated[idx] if translated and idx < len(translated) else v.text
            out.append({
                'book_key': book.book_key,
                'book_name': book.name,
                'chapter': ch.chapter_num,
                'verse': v.verse_num,
                'text': final_text,
            })
        return ApiResponse(data=out)

    @extend_schema(summary=_('高亮'))
    @action(detail=False, methods=['get', 'post'], url_path='highlights')
    def highlights(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user)
        uid = request.user.id
        if request.method == 'GET':
            qs = BibleHighlight.objects.filter(user_id=uid, app_key=app).order_by('-id')
            return ApiResponse(data=[{
                'id': h.id, 'translation_code': h.translation_code,
                'book_key': h.book_key, 'chapter': h.chapter, 'verse': h.verse,
                'color': h.color,
            } for h in qs[:200]])
        if request.data.get('delete_id'):
            BibleHighlight.objects.filter(
                id=request.data['delete_id'], user_id=uid, app_key=app,
            ).delete()
            return ApiResponse(message='deleted')
        book_key = str(request.data.get('book_key') or '')
        try:
            chapter = int(request.data.get('chapter') or 0)
            verse = int(request.data.get('verse') or 0)
        except (TypeError, ValueError):
            return ApiResponse(message='invalid chapter/verse', code=400)
        if not book_key or chapter < 1 or verse < 1:
            return ApiResponse(message='book_key/chapter/verse required', code=400)
        color = str(request.data.get('color') or 'gold')[:32] or 'gold'
        trans = str(request.data.get('translation_code') or '')[:32]
        existing = BibleHighlight.objects.filter(
            user_id=uid, app_key=app, translation_code=trans,
            book_key=book_key[:64], chapter=chapter, verse=verse,
        ).first()
        if existing:
            existing.color = color
            existing.save(update_fields=['color', 'updated_at'])
            return ApiResponse(data={'id': existing.id, 'color': existing.color})
        h = BibleHighlight.objects.create(
            user_id=uid, app_key=app,
            translation_code=trans,
            book_key=book_key[:64], chapter=chapter, verse=verse,
            color=color,
        )
        return ApiResponse(data={'id': h.id, 'color': h.color}, code=201)

    @extend_schema(summary=_('笔记'))
    @action(detail=False, methods=['get', 'post'], url_path='notes')
    def notes(self, request):
        set_config_context_from_request(request)
        app, _, _ = _scope(request.user)
        uid = request.user.id
        if request.method == 'GET':
            qs = BibleNote.objects.filter(user_id=uid, app_key=app).order_by('-id')
            return ApiResponse(data=[{
                'id': n.id, 'translation_code': n.translation_code,
                'book_key': n.book_key, 'chapter': n.chapter, 'verse': n.verse,
                'body': n.body,
            } for n in qs[:200]])
        if request.data.get('delete_id'):
            BibleNote.objects.filter(
                id=request.data['delete_id'], user_id=uid, app_key=app,
            ).delete()
            return ApiResponse(message='deleted')
        body = str(request.data.get('body') or '')
        book_key = str(request.data.get('book_key') or '')
        try:
            chapter = int(request.data.get('chapter') or 0)
            verse = int(request.data.get('verse') or 0)
        except (TypeError, ValueError):
            return ApiResponse(message='invalid chapter/verse', code=400)
        if not body or not book_key:
            return ApiResponse(message='body/book_key required', code=400)
        if request.data.get('id'):
            BibleNote.objects.filter(
                id=request.data['id'], user_id=uid, app_key=app,
            ).update(body=body)
            return ApiResponse(data={'id': request.data['id']})
        n = BibleNote.objects.create(
            user_id=uid, app_key=app,
            translation_code=str(request.data.get('translation_code') or '')[:32],
            book_key=book_key[:64], chapter=chapter, verse=verse, body=body,
        )
        return ApiResponse(data={'id': n.id}, code=201)

    @extend_schema(summary=_('经文 Amen'))
    @action(detail=False, methods=['get', 'post'], url_path='amen-verse')
    def amen_verse(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        if request.method == 'GET':
            qs = BibleAmen.objects.filter(
                user_id=request.user.id, app_key=app, scene='bible',
            )
            book_key = str(request.query_params.get('book_key') or '')[:64]
            if book_key:
                qs = qs.filter(book_key=book_key)
            try:
                chapter = int(request.query_params.get('chapter') or 0)
            except (TypeError, ValueError):
                chapter = 0
            if chapter:
                qs = qs.filter(chapter=chapter)
            rows = qs.values('verse').annotate(n=Count('id'))
            return ApiResponse(data={str(r['verse']): int(r['n']) for r in rows if r['verse']})
        try:
            chapter = int(request.data.get('chapter') or 0) or None
            verse = int(request.data.get('verse') or 0) or None
        except (TypeError, ValueError):
            chapter = verse = None
        BibleAmen.objects.create(
            user_id=request.user.id, app_key=app,
            translation_code=str(request.data.get('translation_code') or '')[:32],
            book_key=str(request.data.get('book_key') or '')[:64],
            chapter=chapter, verse=verse,
            scene='bible',
            ref=str(request.data.get('ref') or '')[:255],
        )
        count = BibleAmen.objects.filter(
            user_id=request.user.id, app_key=app, scene='bible',
            book_key=str(request.data.get('book_key') or '')[:64],
            chapter=chapter, verse=verse,
        ).count()
        newly = evaluate_manna_badges(request.user, app, language=lang, platform=plat)
        return ApiResponse(data={'ok': True, 'count': count, 'newly_earned': newly}, code=201)

    @extend_schema(summary=_('阅读进度上报'))
    @action(detail=False, methods=['post'], url_path='read-progress')
    def read_progress(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        sec = int(request.data.get('seconds') or request.data.get('sec') or 0)
        # 业务日以服务端 + 请求 tz 为准（勿信客户端 local_date）
        local_dt, _tz = user_local_now(request.user, request.data.get('tz'))
        local_date = local_dt.date()
        sec = max(0, sec)
        if sec <= 0:
            return ApiResponse(data={'ok': True, 'manna_delta': 0, 'read_sec': 0, 'newly_earned': []})
        flags = {'book': str(request.data.get('book_key') or ''), 'chapter': request.data.get('chapter')}
        if sec >= 60:
            flags['read'] = True
            flags['bible'] = True
        touch_activity_day(
            request.user, app, day=local_date, read_sec=sec,
            flags=flags,
        )
        grant = grant_bible_read_reward(request.user, app, local_date=local_date)
        newly = evaluate_manna_badges(request.user, app, language=lang, platform=plat)
        return ApiResponse(data={
            'ok': True,
            'manna_delta': grant['manna_delta'],
            'balance': grant['balance'],
            'read_sec': grant['read_sec'],
            'newly_earned': newly,
        })

    @extend_schema(summary=_('Google Translate API 实时翻译经文（不落库）'))
    @action(detail=False, methods=['post'], url_path='translate')
    def translate(self, request):
        """实时调用 Google Translate API；译文只返回给客户端当页展示，不落库、不作为译本保存。"""
        import json
        import os
        import urllib.error
        import urllib.parse

        from tools.http_retry import http_get, http_json

        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        # Header 优先：与其它 Bible API 一致，避免串 Selah / 错语言 / 错端
        headers = getattr(request, 'headers', None) or {}
        hdr_app = normalize_app_key(
            headers.get('X-App-Key') or headers.get('app-key') or ''
        )
        if hdr_app:
            app = hdr_app
        hdr_lang = normalize_lang(
            (headers.get('Accept-Language') or '').split(',')[0] or ''
        )
        if hdr_lang:
            lang = hdr_lang
        hdr_plat = normalize_platform(
            headers.get('X-Platform') or headers.get('platform') or ''
        )
        if hdr_plat:
            plat = hdr_plat
        set_config_context(lang, plat, app)

        if not config_as_bool('translate_enabled', True):
            return ApiResponse(message=_('Translation disabled'), code=403)

        texts = request.data.get('texts') or []
        if not isinstance(texts, list):
            return ApiResponse(message='texts must be a list', code=400)
        texts = [str(x or '') for x in texts][:80]
        if not texts:
            return ApiResponse(message='texts required', code=400)
        title = str(request.data.get('title') or request.data.get('book_name') or '').strip()
        target = str(request.data.get('target') or request.data.get('tl') or lang or 'en').strip()
        allowed = {
            'en': 'en', 'zh': 'zh-CN', 'zh-cn': 'zh-CN', 'zh-tw': 'zh-TW',
            'es': 'es', 'pt': 'pt', 'ja': 'ja', 'ko': 'ko',
        }
        tl = allowed.get(target.lower(), allowed.get(str(lang or 'en').lower(), 'en'))
        source = str(request.data.get('source') or request.data.get('sl') or 'auto').strip() or 'auto'
        api_key = (os.getenv('GOOGLE_TRANSLATE_API_KEY') or '').strip()
        from django.conf import settings as dj_settings

        def _official(chunk):
            q = [x for x in chunk]
            url = 'https://translation.googleapis.com/language/translate/v2?key=%s' % urllib.parse.quote(api_key)
            payload = {'q': q, 'target': tl, 'format': 'text'}
            if source and source != 'auto':
                payload['source'] = source
            _, data = http_json(
                url,
                method='POST',
                data=payload,
                timeout=20,
                retries=2,
            )
            rows = ((data or {}).get('data') or {}).get('translations') or []
            return [str(r.get('translatedText') or '') for r in rows]

        def _gtx(chunk):
            q = '\n'.join(chunk)
            params = urllib.parse.urlencode({
                'client': 'gtx',
                'sl': source,
                'tl': tl,
                'dt': 't',
                'q': q,
            })
            url = f'https://translate.googleapis.com/translate_a/single?{params}'
            _, raw = http_get(
                url,
                headers={'User-Agent': f'MannaBible/1.0 app={app};plat={plat};lang={lang}'},
                timeout=20,
                retries=2,
            )
            data = json.loads(raw)
            parts = []
            for row in (data[0] or []):
                if row and row[0]:
                    parts.append(row[0])
            return ''.join(parts).split('\n')

        if not api_key and not bool(getattr(dj_settings, 'DEBUG', False)):
            return ApiResponse(message=_('Translation not configured'), code=503)

        translate_one = _official if api_key else _gtx
        provider = 'google_v2' if api_key else 'google_gtx'

        to_translate = list(texts)
        if title:
            to_translate = [title] + to_translate

        out = []
        i = 0
        try:
            while i < len(to_translate):
                chunk = to_translate[i:i + 12]
                translated = translate_one(chunk)
                if len(translated) != len(chunk):
                    translated = []
                    for one in chunk:
                        if not one.strip():
                            translated.append(one)
                            continue
                        one_out = translate_one([one])
                        translated.append(one_out[0] if one_out else one)
                out.extend(translated)
                i += 12
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError, KeyError,
                TypeError) as e:
            logger.warning(
                'bible translate failed app=%s lang=%s plat=%s: %s',
                app, lang, plat, e,
            )
            return ApiResponse(message=_('Translation failed'), code=502)

        while len(out) < len(to_translate):
            out.append(to_translate[len(out)])

        translated_title = ''
        verse_translations = out
        if title:
            translated_title = out[0] if out else ''
            verse_translations = out[1:]
        while len(verse_translations) < len(texts):
            verse_translations.append(texts[len(verse_translations)])
        verse_translations = verse_translations[:len(texts)]

        return ApiResponse(data={
            'translations': verse_translations,
            'title': translated_title,
            'book_name': translated_title,
            'target': tl,
            'provider': provider,
            'provider_name': 'Google Translate API',
            'live': True,
            'stored': False,
            'app_key': app,
            'language': lang,
            'platform': plat,
        })

    @extend_schema(summary=_('内容 Tag'))
    @action(detail=False, methods=['get'], url_path='tags')
    def tags(self, request):
        set_config_context_from_request(request)
        app, lang, plat = _scope(request.user)
        rows = filter_scoped_rows(
            list(MannaContentTag.objects.filter(enabled=True).order_by('sort_order', 'id')),
            lang, plat, group_key='key', app_key=app,
        )
        return ApiResponse(data=[{
            'key': r.key,
            'display_name': r.display_name,
            'sort_order': r.sort_order,
            'featured_series_id': r.featured_series_id,
        } for r in rows])
