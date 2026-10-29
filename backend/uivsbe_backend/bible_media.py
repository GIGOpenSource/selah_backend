"""Serve bundled / external Bible chapter audio for Manna."""
from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponseNotAllowed

logger = logging.getLogger(__name__)


def _audio_roots():
    roots = []
    bundled = Path(settings.BASE_DIR).parent.parent / 'content' / 'bible' / 'audio'
    if bundled.is_dir():
        roots.append(bundled)
    env = (os.getenv('BIBLE_CONTENT_ROOT') or '').strip()
    if env:
        p = Path(env) / 'audio'
        if p.is_dir():
            roots.append(p)
    ext = ''
    try:
        from tools.selah_quota import get_config
        ext = (get_config('bible_audio_root', '') or '').strip()
    except Exception:
        ext = ''
    if not ext:
        ext = (os.getenv('BIBLE_AUDIO_ROOT') or '').strip()
    if ext:
        p = Path(ext)
        if p.is_dir():
            roots.append(p)
    return roots


def bible_media(request, relpath: str):
    """GET /bible-media/<version>/<gender>/<Book>/<NNN>.mp3"""
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    try:
        from tools.selah_quota import set_config_context_from_request
        set_config_context_from_request(request)
    except Exception:
        pass
    rel = (relpath or '').replace('\\', '/').lstrip('/')
    if '..' in rel.split('/') or rel.startswith('/'):
        raise Http404()
    if not rel.lower().endswith('.mp3'):
        raise Http404()
    for root in _audio_roots():
        full = (root / rel).resolve()
        try:
            full.relative_to(root.resolve())
        except ValueError:
            continue
        if full.is_file():
            content_type = mimetypes.guess_type(str(full))[0] or 'audio/mpeg'
            return FileResponse(open(full, 'rb'), content_type=content_type)
    parts = rel.split('/')
    if len(parts) >= 2 and parts[1].lower() == 'audio':
        parts[1] = 'male'
        rel = '/'.join(parts)
        for root in _audio_roots():
            full = (root / rel).resolve()
            try:
                full.relative_to(root.resolve())
            except ValueError:
                continue
            if full.is_file():
                content_type = mimetypes.guess_type(str(full))[0] or 'audio/mpeg'
                return FileResponse(open(full, 'rb'), content_type=content_type)
    cdn = (os.getenv('BIBLE_AUDIO_CDN_BASE') or '').strip().rstrip('/')
    if cdn:
        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(cdn + '/' + rel)
    raise Http404()
