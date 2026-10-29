#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""运营台 Staff 可管理 App 范围（User.app_keys）。

空 app_keys = 全部 App；非空则列表/写入仅限所列 App。
写入空维（全局 app_key=''）仅允许「全部 App」账号。
"""
from django.db.models import Q
from django.utils.translation import gettext as _

from tools.selah_quota import normalize_app_key


def staff_managed_apps(user):
    """返回可管理 App 列表；空列表表示不限。"""
    raw = getattr(user, 'app_keys', None) or []
    out = []
    for k in raw:
        ak = normalize_app_key(k)
        if ak and ak not in out:
            out.append(ak)
    return out


def staff_can_manage_app(user, app_key):
    """是否可操作目标 app_key（含空=全局）。"""
    allowed = staff_managed_apps(user)
    if not allowed:
        return True
    ak = normalize_app_key(app_key or '')
    if not ak:
        return False
    return ak in allowed


def deny_staff_app_write(user, app_key):
    """无权限时返回错误消息字符串；有权限返回 None。"""
    if staff_can_manage_app(user, app_key):
        return None
    ak = normalize_app_key(app_key or '')
    if not ak:
        return _('当前账号不可写入全局 App 维，请指定可管理的 App')
    return _('无权限操作该 App')


def apply_staff_app_list_filter(qs, user, field='app_key'):
    """列表：受限账号只看自己的 App + 全局空维行。"""
    allowed = staff_managed_apps(user)
    if not allowed:
        return qs
    return qs.filter(Q(**{f'{field}__in': allowed}) | Q(**{field: ''}))
