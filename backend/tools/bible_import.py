#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""从 content/bible 导入 WEB/KJV 正文与听经元数据。

文字：content/bible/data/{WEB|KJV}/{Book}/{NNN}.json
音频：content/bible/audio/...（全书 WEB/KJV × male/female）；BIBLE_AUDIO_ROOT 仅当音频在仓库外时使用。
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from django.conf import settings

from models.models import BibleBook, BibleChapter, BibleTranslation, BibleVerse
from tools.selah_quota import MANNA_APP_KEY

logger = logging.getLogger(__name__)

# Protestant canon display names as on disk under data/WEB/
BOOKS = [
    ('Genesis', 50, 'ot'),
    ('Exodus', 40, 'ot'),
    ('Leviticus', 27, 'ot'),
    ('Numbers', 36, 'ot'),
    ('Deuteronomy', 34, 'ot'),
    ('Joshua', 24, 'ot'),
    ('Judges', 21, 'ot'),
    ('Ruth', 4, 'ot'),
    ('1 Samuel', 31, 'ot'),
    ('2 Samuel', 24, 'ot'),
    ('1 Kings', 22, 'ot'),
    ('2 Kings', 25, 'ot'),
    ('1 Chronicles', 29, 'ot'),
    ('2 Chronicles', 36, 'ot'),
    ('Ezra', 10, 'ot'),
    ('Nehemiah', 13, 'ot'),
    ('Esther', 10, 'ot'),
    ('Job', 42, 'ot'),
    ('Psalms', 150, 'ot'),  # folder may be "Psalms"; JSON book field "Psalm"
    ('Proverbs', 31, 'ot'),
    ('Ecclesiastes', 12, 'ot'),
    ('Song of Solomon', 8, 'ot'),
    ('Isaiah', 66, 'ot'),
    ('Jeremiah', 52, 'ot'),
    ('Lamentations', 5, 'ot'),
    ('Ezekiel', 48, 'ot'),
    ('Daniel', 12, 'ot'),
    ('Hosea', 14, 'ot'),
    ('Joel', 3, 'ot'),
    ('Amos', 9, 'ot'),
    ('Obadiah', 1, 'ot'),
    ('Jonah', 4, 'ot'),
    ('Micah', 7, 'ot'),
    ('Nahum', 3, 'ot'),
    ('Habakkuk', 3, 'ot'),
    ('Zephaniah', 3, 'ot'),
    ('Haggai', 2, 'ot'),
    ('Zechariah', 14, 'ot'),
    ('Malachi', 4, 'ot'),
    ('Matthew', 28, 'nt'),
    ('Mark', 16, 'nt'),
    ('Luke', 24, 'nt'),
    ('John', 21, 'nt'),
    ('Acts', 28, 'nt'),
    ('Romans', 16, 'nt'),
    ('1 Corinthians', 16, 'nt'),
    ('2 Corinthians', 13, 'nt'),
    ('Galatians', 6, 'nt'),
    ('Ephesians', 6, 'nt'),
    ('Philippians', 4, 'nt'),
    ('Colossians', 4, 'nt'),
    ('1 Thessalonians', 5, 'nt'),
    ('2 Thessalonians', 3, 'nt'),
    ('1 Timothy', 6, 'nt'),
    ('2 Timothy', 4, 'nt'),
    ('Titus', 3, 'nt'),
    ('Philemon', 1, 'nt'),
    ('Hebrews', 13, 'nt'),
    ('James', 5, 'nt'),
    ('1 Peter', 5, 'nt'),
    ('2 Peter', 3, 'nt'),
    ('1 John', 5, 'nt'),
    ('2 John', 1, 'nt'),
    ('3 John', 1, 'nt'),
    ('Jude', 1, 'nt'),
    ('Revelation', 22, 'nt'),
]

_FOLDER_ALIASES = {
    'Psalms': ['Psalms', 'Psalm'],
    'Psalm': ['Psalms', 'Psalm'],
}


def book_key_from_name(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r'[^a-z0-9]+', '_', s).strip('_')
    if s == 'psalm':
        return 'psalms'
    return s


def content_bible_root() -> Path:
    env = (os.getenv('BIBLE_CONTENT_ROOT') or '').strip()
    if env:
        return Path(env)
    # settings.BASE_DIR = .../backend/uivsbe_backend → 仓库根/content/bible
    return Path(settings.BASE_DIR).parent.parent / 'content' / 'bible'


def data_root(version: str) -> Path:
    return content_bible_root() / 'data' / version.upper()


def audio_search_roots(version: str, gender: str = 'male') -> list[Path]:
    """优先项目内 content/bible/audio，其次 BIBLE_AUDIO_ROOT（音频在仓库外时）。"""
    roots = []
    bundled = content_bible_root() / 'audio' / version.upper() / gender
    if bundled.is_dir():
        roots.append(bundled)
    ext = ''
    try:
        from tools.selah_quota import get_config
        ext = (get_config('bible_audio_root', '', app_key='manna') or '').strip()
    except Exception:
        ext = ''
    if not ext:
        ext = (os.getenv('BIBLE_AUDIO_ROOT') or '').strip()
    if ext:
        p = Path(ext) / version.upper() / gender
        if p.is_dir():
            roots.append(p)
        # Desktop layout: .../audio/WEB/male
        p2 = Path(ext) / gender
        if p2.is_dir():
            roots.append(p2)
    return roots


def resolve_book_dir(version: str, display_name: str) -> Path | None:
    base = data_root(version)
    candidates = _FOLDER_ALIASES.get(display_name, [display_name])
    for name in candidates:
        d = base / name
        if d.is_dir():
            return d
    return None


def load_chapter_json(book_dir: Path, chapter_num: int) -> dict | None:
    path = book_dir / f'{chapter_num:03d}.json'
    if not path.is_file():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def find_audio_file(version: str, book_folder: str, chapter_num: int, gender: str = 'male') -> Path | None:
    rel = Path(book_folder) / f'{chapter_num:03d}.mp3'
    for root in audio_search_roots(version, gender):
        p = root / rel
        if p.is_file():
            return p
        # alias Psalm/Psalms
        for alt in _FOLDER_ALIASES.get(book_folder, []):
            p2 = root / alt / f'{chapter_num:03d}.mp3'
            if p2.is_file():
                return p2
    return None


def public_audio_url(version: str, book_folder: str, chapter_num: int, gender: str = 'male') -> str:
    """App 可请求的相对路径（由 /bible-media/ 提供）。"""
    return f'/bible-media/{version.upper()}/{gender}/{book_folder}/{chapter_num:03d}.mp3'


def estimate_duration_sec(path: Path | None) -> int:
    if not path or not path.is_file():
        return 0
    # rough: mp3 ~16KB/s for speech demos; better than 0 for UI
    try:
        size = path.stat().st_size
        return max(1, int(size / 16000))
    except OSError:
        return 0


def ensure_translation(code: str, title: str, app_key: str = MANNA_APP_KEY, language: str = 'en'):
    tr, _ = BibleTranslation.objects.update_or_create(
        code=code.lower(), app_key=app_key, language=language, platform='',
        defaults={'title': title, 'enabled': True, 'sort_order': 0 if code.lower() == 'web' else 1},
    )
    return tr


def import_chapter(
    *,
    version: str,
    display_name: str,
    chapter_num: int,
    testament: str,
    order_index: int,
    app_key: str = MANNA_APP_KEY,
    language: str = 'en',
    gender: str = 'male',
    attach_audio: bool = True,
) -> dict:
    book_dir = resolve_book_dir(version, display_name)
    if not book_dir:
        return {'ok': False, 'reason': 'book_dir_missing', 'book': display_name}
    payload = load_chapter_json(book_dir, chapter_num)
    if not payload:
        return {'ok': False, 'reason': 'json_missing', 'book': display_name, 'chapter': chapter_num}
    verses = payload.get('verses') or []
    if not verses:
        return {'ok': False, 'reason': 'empty_verses', 'book': display_name, 'chapter': chapter_num}

    code = version.lower()
    title = 'World English Bible' if code == 'web' else 'King James Version'
    tr = ensure_translation(code, title, app_key=app_key, language=language)
    bkey = book_key_from_name(display_name)
    folder_name = book_dir.name
    tags = [testament]
    if bkey in ('genesis', 'exodus', 'leviticus', 'numbers', 'deuteronomy'):
        tags.append('pentateuch')
    if bkey in ('matthew', 'mark', 'luke', 'john'):
        tags.append('gospel')

    book, _ = BibleBook.objects.update_or_create(
        translation_id=tr.id, book_key=bkey,
        defaults={
            'name': display_name if display_name != 'Psalms' else 'Psalms',
            'order_index': order_index,
            'testament': testament,
            'tag_keys': tags,
        },
    )

    audio_url = ''
    duration = 0
    if attach_audio:
        audio_path = find_audio_file(version, folder_name, chapter_num, gender=gender)
        if audio_path:
            audio_url = public_audio_url(version, folder_name, chapter_num, gender=gender)
            duration = estimate_duration_sec(audio_path)

    ch, _ = BibleChapter.objects.update_or_create(
        book_id=book.id, chapter_num=chapter_num,
        defaults={'audio_url': audio_url, 'audio_duration_sec': duration},
    )
    count = 0
    for v in verses:
        try:
            vnum = int(v.get('verse') or v.get('verse_num') or 0)
        except (TypeError, ValueError):
            continue
        text = str(v.get('text') or '').strip()
        if vnum < 1 or not text:
            continue
        BibleVerse.objects.update_or_create(
            chapter_id=ch.id, verse_num=vnum, defaults={'text': text},
        )
        count += 1
    return {
        'ok': True,
        'translation_id': tr.id,
        'book_id': book.id,
        'chapter_id': ch.id,
        'verse_count': count,
        'audio_url': audio_url,
    }


def import_version(
    version: str = 'WEB',
    *,
    books: list[str] | None = None,
    chapters: dict[str, list[int]] | None = None,
    full: bool = False,
    app_key: str = MANNA_APP_KEY,
    language: str = 'en',
) -> dict:
    """
    books: 限制书卷显示名；None 且 full=False 时默认 Genesis + John。
    chapters: { 'Genesis': [1,2], ... }；缺省则导入该书全部章（若 full/该书在 books）。
    """
    version = version.upper()
    if books is None and not full:
        books = ['Genesis', 'John']
    allow = {b.lower() for b in books} if books else None
    stats = {'chapters': 0, 'verses': 0, 'skipped': 0, 'errors': []}

    for idx, (name, n_chapters, testament) in enumerate(BOOKS, start=1):
        if allow is not None and name.lower() not in allow and book_key_from_name(name) not in allow:
            # also match folder alias
            if name == 'Psalms' and allow and 'psalms' in allow:
                pass
            else:
                continue
        chap_list = None
        if chapters and name in chapters:
            chap_list = chapters[name]
        elif chapters and book_key_from_name(name) in chapters:
            chap_list = chapters[book_key_from_name(name)]
        elif not full and allow is not None:
            # default seed: chapter 1 only when not full
            chap_list = [1]
        else:
            chap_list = list(range(1, n_chapters + 1))

        for cnum in chap_list:
            res = import_chapter(
                version=version,
                display_name=name,
                chapter_num=cnum,
                testament=testament,
                order_index=idx,
                app_key=app_key,
                language=language,
            )
            if res.get('ok'):
                stats['chapters'] += 1
                stats['verses'] += int(res.get('verse_count') or 0)
            else:
                stats['skipped'] += 1
                if res.get('reason') not in ('json_missing',):
                    stats['errors'].append(res)
    return stats


def import_seed_core(app_key: str = MANNA_APP_KEY, language: str = 'en') -> dict:
    """Phase B 默认：WEB+KJV 的 Genesis 1 与 John 1（含本地种子音频）。"""
    out = {}
    for ver in ('WEB', 'KJV'):
        out[ver] = import_version(
            ver,
            books=['Genesis', 'John'],
            chapters={'Genesis': [1], 'John': [1]},
            full=False,
            app_key=app_key,
            language=language,
        )
    return out


def import_full_text(version: str = 'WEB', app_key: str = MANNA_APP_KEY, language: str = 'en') -> dict:
    """导入某译本全部章节正文（音频有则挂）。耗时较长。"""
    return import_version(version, full=True, app_key=app_key, language=language)
