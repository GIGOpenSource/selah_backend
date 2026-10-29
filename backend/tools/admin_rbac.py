#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
运营后台角色权限（基于 role 字符串 + AppConfig 覆盖，无独立 RBAC 表）。

角色 STAFF_ROLES：operator / admin / super_admin
权限码 ALL_PERMS（菜单/模块级，非按钮级）：
  content     — 内容管理（语录、壁纸、话题等）
  ops         — 运营配置（AppConfig、Onboarding、快捷语、安全文案等）
  app_users   — 用户侧 App 用户管理（查询/禁言等，非后台账号）
  staff       — 后台用户管理（创建/改角色 operator、admin）
  permissions — 权限矩阵管理（改 admin_role_permissions）

默认矩阵 DEFAULT_ROLE_PERMS；运行时覆盖存 AppConfig key=admin_role_permissions。
约束：super_admin 始终全权限，避免自锁。
"""
from tools.selah_quota import get_config, set_config

# ---------------------------------------------------------------------------
# 菜单/模块权限码：与前端路由 meta.perm、接口装饰校验共用同一字符串
# ---------------------------------------------------------------------------
PERM_CONTENT = 'content'          # 内容管理
PERM_OPS = 'ops'                  # 运营配置
PERM_APP_USERS = 'app_users'      # App 端用户
PERM_STAFF = 'staff'              # 后台账号
PERM_PERMISSIONS = 'permissions'  # 角色权限矩阵本身

ALL_PERMS = [
    PERM_CONTENT,
    PERM_OPS,
    PERM_APP_USERS,
    PERM_STAFF,
    PERM_PERMISSIONS,
]

PERM_LABELS = {
    PERM_CONTENT: '内容管理',
    PERM_OPS: '运营配置',
    PERM_APP_USERS: '用户侧用户管理',
    PERM_STAFF: '后台用户管理',
    PERM_PERMISSIONS: '权限管理',
}

# operator：一线运营；admin：可管后台账号；super_admin：超管不可降权锁死
STAFF_ROLES = ('operator', 'admin', 'super_admin')

ROLE_LABELS = {
    'operator': '运营',
    'admin': '管理员',
    'super_admin': '超级管理员',
}

# 默认矩阵：改代码即改默认；线上覆盖写在 AppConfig，不改默认常量
DEFAULT_ROLE_PERMS = {
    'operator': [PERM_CONTENT, PERM_OPS, PERM_APP_USERS],
    'admin': [PERM_CONTENT, PERM_OPS, PERM_APP_USERS, PERM_STAFF],
    'super_admin': list(ALL_PERMS),
}

CONFIG_KEY = 'admin_role_permissions'


def get_role_permissions_map():
    """
    返回 {role: [perm, ...]}，合并默认与配置覆盖。

    stored 非 dict 或非法 role/perm 则忽略该项；
    super_admin 无论配置如何都强制 ALL_PERMS。
    """
    stored = get_config(CONFIG_KEY, None)
    result = {k: list(v) for k, v in DEFAULT_ROLE_PERMS.items()}
    if isinstance(stored, dict):
        for role, perms in stored.items():
            if role not in STAFF_ROLES:
                continue
            if not isinstance(perms, list):
                continue
            cleaned = [p for p in perms if p in ALL_PERMS]
            # super_admin 始终拥有全部权限，避免锁死自己
            if role == 'super_admin':
                cleaned = list(ALL_PERMS)
            result[role] = cleaned
    return result


def permissions_for_role(role):
    """某角色的权限码列表；非后台角色（如 user）返回 []。"""
    if role not in STAFF_ROLES:
        return []
    return list(get_role_permissions_map().get(role, []))


def has_perm(user, perm):
    """
    用户是否拥有 perm。

    - 非后台角色 / 无 user → False
    - super_admin → 恒 True（短路，不读配置）
    - 其余 → perm in permissions_for_role(role)
    """
    if not user or getattr(user, 'role', None) not in STAFF_ROLES:
        return False
    if user.role == 'super_admin':
        return True
    return perm in permissions_for_role(user.role)


def save_role_permissions(role_map):
    """
    保存覆盖配置到 AppConfig；强制 super_admin 全权限。

    非法 perm 丢弃；某 role 缺省或类型不对则回落 DEFAULT_ROLE_PERMS。
    """
    cleaned = {}
    for role in STAFF_ROLES:
        perms = role_map.get(role) if isinstance(role_map, dict) else None
        if not isinstance(perms, list):
            perms = DEFAULT_ROLE_PERMS.get(role, [])
        cleaned[role] = list(ALL_PERMS) if role == 'super_admin' else [p for p in perms if p in ALL_PERMS]
    set_config(CONFIG_KEY, cleaned, remark='运营角色权限矩阵')
    return cleaned


def permission_catalog():
    """权限码目录 [{code, label}, ...]，供运营台展示勾选。"""
    return [{'code': c, 'label': PERM_LABELS[c]} for c in ALL_PERMS]
