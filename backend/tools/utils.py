"""
通用 API 响应与分页、异常处理、OpenAPI 钩子。

- ApiResponse：统一 {code, message, results[, pagination, meta]}，HTTP 状态恒 200
- CustomPagination：currentPage / pageSize；越界页返回空列表而非 404
- custom_exception_handler：Invalid page → 空分页成功响应
- exclude_api_tag_hook：Spectacular 去掉默认 api 标签

业务约定：前端只看 body.code / body.message；HTTP 层几乎总是 200。
"""
import json
import logging
from asyncio import Lock
from urllib.request import urlopen, Request
from urllib.error import URLError

from django.core.paginator import EmptyPage, InvalidPage, Page, Paginator
from django.utils.translation import gettext as _
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler
import os
from logging.handlers import TimedRotatingFileHandler

logger = logging.getLogger(__name__)


def _is_invalid_page_error(exc):
    """DRF/Django 无效页码（含中英文文案）。"""
    if isinstance(exc, (EmptyPage, InvalidPage)):
        return True
    if not isinstance(exc, NotFound):
        return False
    detail = str(getattr(exc, 'detail', exc) or '')
    return 'Invalid page' in detail or '无效页面' in detail

class ApiResponse(Response):
    """
    统一业务响应。

    Body 形状：
      {
        "code": int,          # 业务码，成功多为 200/201；错误 4xx/5xx 风格
        "message": str,       # 提示文案；dict 校验错误会被拼成 \"k: v; ...\"
        "results": any,       # 主数据；None 会改成 {}
        "pagination"?: {...}, # 可选分页元信息
        "meta"?: {...},       # 可选扩展
        "followed"?: bool     # 可选顶层字段（如自我肯定整体关注态）
      }

    HTTP status 固定 200，避免网关/客户端只认 HTTP 码时丢业务错误细节。
    """
    def __init__(self, data=None, message='success', code=200, pagination=None, meta=None, followed=None, **kwargs):
        # 确保message是字符串类型
        if not isinstance(message, str):
            # 如果是字典类型（如表单验证错误），转换为字符串
            if isinstance(message, dict):
                # 将字典转换为用分号分隔的键值对字符串
                message = "; ".join([f"{k}: {', '.join(v)}" for k, v in message.items()])
            else:
                # 其他类型强制转换为字符串
                message = str(message)
        # 确保data不为null，如果为null则设为空字典
        if data is None:
            data = {}

        response_data = {
            'code': code,
            'message': message,
            'results': data
        }

        if pagination is not None:
            response_data["pagination"] = pagination
        if meta is not None:
            response_data["meta"] = meta
        if followed is not None:
            response_data["followed"] = bool(followed)
        # 始终使用200作为HTTP状态码
        super().__init__(response_data, status=200, **kwargs)



class CustomPagination(PageNumberPagination):
    """
    运营/App 列表分页：查询参数 currentPage、pageSize。

    成功时 pagination = {page, page_size, total, total_pages}，与 results 同级挂在 ApiResponse。

    注意：管理端 el-pagination 未绑定 page-size，默认按 10 计算页数，
    但请求常带 pageSize=20，会导致页码约为真实的两倍、后半段全是空页。
    因此将 pageSize=20 归一为 10；其它显式值（如 100/999）仍原样生效。
    """
    page_size = 10  # 与管理端 el-pagination 默认 page-size 对齐
    page_query_param = 'currentPage'  # 关键：匹配前端的 "currentPage" 参数（指定页码）
    page_size_query_param = 'pageSize'  # 匹配前端的 "pageSize" 参数（指定每页条数）
    max_page_size = 999  # 最大每页条数限制
    # 管理端列表请求里的历史默认值，需按 UI 默认 10 条/页纠正
    _admin_ui_mismatch_page_size = 20

    def get_page_size(self, request):
        size = super().get_page_size(request)
        if size == self._admin_ui_mismatch_page_size:
            return self.page_size
        return size

    def get_paginated_response(self, data, meta=None, followed=None):
        # 现在这个方法会在分页生效时被自动调用
        pagination_info = {
            'page': self.page.number,
            'page_size': self.page.paginator.per_page,
            'total': self.page.paginator.count,
            'total_pages': self.page.paginator.num_pages
        }
        # data直接就是列表，pagination同级传入ApiResponse；meta/followed 可选
        return ApiResponse(data=data, pagination=pagination_info, meta=meta, followed=followed)

    def paginate_queryset(self, queryset, request, view=None):
        """页码越界时返回空列表，保留真实 total，避免 Invalid page / 无效页面 变成 404。"""
        page_size = self.get_page_size(request)
        if not page_size:
            return None

        paginator = self.django_paginator_class(queryset, page_size)
        page_number = self.get_page_number(request, paginator)
        self.request = request

        try:
            self.page = paginator.page(page_number)
        except InvalidPage:
            # 保留真实总数，便于前端分页器回落到合法页
            try:
                number = int(page_number)
            except (TypeError, ValueError):
                number = 1
            if paginator.num_pages < 1:
                empty = Paginator([], page_size)
                self.page = empty.page(1)
            else:
                self.page = Page([], number, paginator)
            return []

        if paginator.num_pages > 1 and self.template is not None:
            self.display_page_controls = True
        return list(self.page)


def custom_exception_handler(exc, context):
    """
    DRF 异常钩子：无效页码转为空结果 ApiResponse，其余走默认 handler。

    注意：默认 handler 返回的仍是标准 DRF Response，未必是 ApiResponse 形状；
    业务 View 应尽量自行 ApiResponse 包一层。
    """
    if _is_invalid_page_error(exc):
        return ApiResponse(
            data=[],
            pagination={
                'page': 1,
                'page_size': 10,
                'total': 0,
                'total_pages': 0,
            },
            message=_('请求的页面超出范围，返回空结果'),
            code=200,
        )
    from rest_framework.exceptions import AuthenticationFailed
    if isinstance(exc, AuthenticationFailed):
        detail = getattr(exc, 'detail', None)
        if isinstance(detail, dict) and detail.get('code') == 403:
            return ApiResponse(
                message=detail.get('message') or _('账号已禁用'),
                code=403,
            )
    return exception_handler(exc, context)


def exclude_api_tag_hook(endpoints=None, **kwargs):
    """Spectacular preprocessing：去掉默认 'api' 标签，避免文档分组噪音。"""
    if endpoints is None:
        endpoints = []

    # 创建新的端点列表
    filtered_endpoints = []

    for endpoint in endpoints:
        logger.debug('Endpoint: %s', endpoint)

        # 获取操作对象（第三个元素）
        operation = endpoint[2]
        logger.debug('Operation: %s', operation)

        # 检查 operation 是否为字典且包含 tags 字段
        if isinstance(operation, dict) and 'tags' in operation:
            # 如果包含 'api' 标签，则移除
            if 'api' in operation['tags']:
                logger.debug('找到了tags %s', operation['tags'])
                operation['tags'] = [tag for tag in operation['tags'] if tag != 'api']

                # 如果标签为空，则移除整个标签字段
                if not operation['tags']:
                    del operation['tags']

        # 添加到过滤后的端点列表
        filtered_endpoints.append(endpoint)

    return filtered_endpoints


# ---------------------------------------------------------------------------
# 请求工具：平台识别 & IP 国家推断
# ---------------------------------------------------------------------------

def get_client_ip(request):
    """从请求中提取客户端真实 IP（兼容反向代理/CDN）。"""
    for header in ('HTTP_X_REAL_IP', 'HTTP_X_FORWARDED_FOR'):
        val = request.META.get(header, '')
        if val:
            return val.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def resolve_platform_from_request(request):
    """
    从请求头解析平台（客户端未传 platform 时的兜底）。

    优先级：
    1. Sec-CH-UA-Platform（浏览器 Client Hints，如 "Android"/"iOS"/"Windows"/"macOS"）
    2. User-Agent 匹配
    返回 ios / android / web，无法识别返回空串。
    """
    # 1. Client Hints
    ch = (request.META.get('HTTP_SEC_CH_UA_PLATFORM', '') or '').strip().strip('"')
    if ch:
        low = ch.lower()
        if low in ('android',):
            return 'android'
        if low in ('ios', 'ipados'):
            return 'ios'
        if low in ('windows', 'macos', 'linux', 'chromeos', 'chromium os'):
            return 'web'

    # 2. User-Agent 兜底
    ua = (request.META.get('HTTP_USER_AGENT', '') or '').lower()
    if 'android' in ua:
        return 'android'
    if 'iphone' in ua or 'ipad' in ua:
        return 'ios'

    return ''


def resolve_country_from_ip(request, fallback=''):
    """
    从请求推断用户国家二字码（ISO 3166-1 alpha-2）。

    优先级：
    1. CDN/代理头（Cloudflare CF-IPCountry 等）
    2. 免费 IP 地理查询（ip-api.com，无需 key，限 45 次/分钟）
    3. 返回 fallback
    """
    # 1. CDN 直接注入的国家码
    for header in ('HTTP_CF_IPCOUNTRY', 'HTTP_X_APPENGINE_COUNTRY', 'HTTP_X_COUNTRY_CODE'):
        val = (request.META.get(header) or '').strip().upper()
        if val and len(val) == 2 and val != 'XX':
            return val

    # 2. IP 查询
    ip = get_client_ip(request)
    if not ip or ip in ('127.0.0.1', '::1', ''):
        return fallback
    try:
        req = Request(
            f'http://ip-api.com/json/{ip}?fields=countryCode',
            headers={'User-Agent': 'selah-backend/1.0'},
        )
        with urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            cc = (data.get('countryCode') or '').strip().upper()
            if cc and len(cc) == 2:
                return cc
    except (URLError, OSError, json.JSONDecodeError, KeyError):
        pass

    return fallback
