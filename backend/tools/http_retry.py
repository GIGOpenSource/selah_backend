#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""带超时与有限次重试的 HTTP JSON 请求。"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def http_json(
    url,
    *,
    method='GET',
    data=None,
    headers=None,
    timeout=20,
    retries=2,
    backoff=0.4,
):
    """GET/POST JSON；瞬时失败重试 retries 次。成功返回 (status, obj)；失败抛 URLError。"""
    body = None
    if data is not None:
        body = json.dumps(data).encode('utf-8') if not isinstance(data, (bytes, bytearray)) else data
    hdrs = {'Accept': 'application/json'}
    if headers:
        hdrs.update(headers)
    if body is not None and 'Content-Type' not in hdrs:
        hdrs['Content-Type'] = 'application/json'
    last_err = None
    attempts = max(1, int(retries) + 1)
    for i in range(attempts):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8') or '{}'
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = {'raw': raw[:500]}
                return resp.status, parsed
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            logger.warning('http_json attempt=%s/%s url=%s err=%s', i + 1, attempts, url, e)
            if i < attempts - 1:
                time.sleep(backoff * (i + 1))
    raise last_err


def http_get(
    url,
    *,
    headers=None,
    timeout=20,
    retries=2,
    backoff=0.4,
):
    """GET 原文；瞬时失败重试。成功返回 (status, text)。"""
    hdrs = dict(headers or {})
    last_err = None
    attempts = max(1, int(retries) + 1)
    for i in range(attempts):
        req = urllib.request.Request(url, headers=hdrs, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode('utf-8')
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            logger.warning('http_get attempt=%s/%s url=%s err=%s', i + 1, attempts, url, e)
            if i < attempts - 1:
                time.sleep(backoff * (i + 1))
    raise last_err
