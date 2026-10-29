#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""账号注销：冷静期申请 / 撤销 / 到期软删。"""
from __future__ import annotations

import logging
from datetime import timedelta
from math import ceil
from typing import Any

from django.db import transaction
from django.utils import timezone

from models.models import User
from tools.token_tools import CustomTokenTool

logger = logging.getLogger(__name__)

DELETION_COOLING_DAYS = 7


def deletion_pending_fields(user: User) -> dict[str, Any]:
    """供 user_public_dict / 登录响应：冷静期提示字段。"""
    if getattr(user, 'is_deleted', False):
        return {
            'deletion_pending': False,
            'deletion_requested_at': None,
            'deletion_scheduled_at': None,
            'days_left': None,
        }
    scheduled = getattr(user, 'deletion_scheduled_at', None)
    requested = getattr(user, 'deletion_requested_at', None)
    if not scheduled or not requested:
        return {
            'deletion_pending': False,
            'deletion_requested_at': None,
            'deletion_scheduled_at': None,
            'days_left': None,
        }
    now = timezone.now()
    remaining = (scheduled - now).total_seconds()
    days_left = max(0, int(ceil(remaining / 86400))) if remaining > 0 else 0
    return {
        'deletion_pending': True,
        'deletion_requested_at': requested.isoformat(),
        'deletion_scheduled_at': scheduled.isoformat(),
        'days_left': days_left,
    }


def request_account_deletion(user: User) -> dict[str, Any]:
    """进入 7 天冷静期；踢全部会话。返回冷静期字段。"""
    if getattr(user, 'is_deleted', False):
        raise ValueError('account_already_deleted')
    if getattr(user, 'role', 'user') != 'user':
        raise ValueError('admin_cannot_self_delete')
    if getattr(user, 'deletion_scheduled_at', None):
        raise ValueError('deletion_already_pending')

    now = timezone.now()
    scheduled = now + timedelta(days=DELETION_COOLING_DAYS)
    user.deletion_requested_at = now
    user.deletion_scheduled_at = scheduled
    user.save(update_fields=[
        'deletion_requested_at', 'deletion_scheduled_at', 'updated_at',
    ])
    CustomTokenTool.delete_user_all_tokens(user.id)
    logger.info(
        'account_deletion_requested user=%s scheduled_at=%s',
        user.id, scheduled.isoformat(),
    )
    return deletion_pending_fields(user)


def cancel_account_deletion(user: User) -> None:
    """冷静期内撤销注销。"""
    if getattr(user, 'is_deleted', False):
        raise ValueError('account_already_deleted')
    if not getattr(user, 'deletion_scheduled_at', None):
        raise ValueError('no_pending_deletion')

    user.deletion_requested_at = None
    user.deletion_scheduled_at = None
    user.save(update_fields=[
        'deletion_requested_at', 'deletion_scheduled_at', 'updated_at',
    ])
    logger.info('account_deletion_cancelled user=%s', user.id)


def soft_delete_user(user: User) -> None:
    """到期软删：释放身份键、踢 token；业务关联行保留。"""
    if getattr(user, 'is_deleted', False):
        return

    now = timezone.now()
    with transaction.atomic():
        user.is_deleted = True
        user.deleted_at = now
        user.device_id = None
        user.google_id = None
        user.apple_id = None
        user.email = None
        user.username = f'deleted_{user.id}'
        user.save(update_fields=[
            'is_deleted', 'deleted_at',
            'device_id', 'google_id', 'apple_id', 'email', 'username',
            'updated_at',
        ])
    CustomTokenTool.delete_user_all_tokens(user.id)
    logger.info('account_soft_deleted user=%s', user.id)


def purge_due_deletions(*, limit: int = 500) -> int:
    """扫描到期冷静期用户并软删；返回处理条数。"""
    now = timezone.now()
    qs = (
        User.objects.filter(
            is_deleted=False,
            deletion_scheduled_at__isnull=False,
            deletion_scheduled_at__lte=now,
        )
        .order_by('deletion_scheduled_at')[:limit]
    )
    count = 0
    for user in qs:
        try:
            soft_delete_user(user)
            count += 1
        except Exception:
            logger.exception('purge soft_delete failed user=%s', user.id)
    return count
