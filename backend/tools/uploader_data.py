# -*- coding=utf-8 -*-
"""对象存储上传：优先 Google Cloud Storage，未配 GCS 时回退腾讯云 COS。

GCS 环境变量：
- GCS_BUCKET          自有 GCS 桶名
- GCS_PREFIX          环境前缀，如 uploads/dev、uploads/prod
- GCS_SERVICE_ACCOUNT_JSON  服务账号 JSON（字符串或文件路径）
- GOOGLE_APPLICATION_CREDENTIALS  服务账号 JSON 文件路径（ADC）

禁止写入 bible-media/、static/。
"""
import json
import logging
import mimetypes
import os
import uuid

from rest_framework.exceptions import AuthenticationFailed
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiParameter, extend_schema

from tools.permissions import IsAdmin, IsTokenValid
from tools.tools import getEnvConfig
from .utils import ApiResponse

logger = logging.getLogger(__name__)

RESERVED_PREFIXES = ('bible-media', 'static')
DEFAULT_GCS_BUCKET = os.getenv('GCS_BUCKET', 'your-gcs-bucket')
DEFAULT_GCS_PREFIX = 'uploads/dev'

gcs_client = None
gcs_bucket_name = (os.getenv('GCS_BUCKET') or DEFAULT_GCS_BUCKET).strip()
gcs_prefix = (os.getenv('GCS_PREFIX') or DEFAULT_GCS_PREFIX).strip().strip('/')

cos_client = None
cos_region = getEnvConfig('TENCENT_COS_REGION')
cos_bucket = getEnvConfig('TENCENT_COS_BUCKET')


def _load_gcs_info():
    raw = (os.getenv('GCS_SERVICE_ACCOUNT_JSON') or '').strip()
    if raw:
        if os.path.isfile(raw):
            with open(raw, encoding='utf-8') as f:
                return json.load(f)
        if raw.startswith('{'):
            return json.loads(raw)
    path = (os.getenv('GOOGLE_APPLICATION_CREDENTIALS') or '').strip()
    if path and os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return None


def _init_gcs():
    global gcs_client
    try:
        from google.cloud import storage
        from google.oauth2 import service_account
    except ImportError:
        logger.warning('未安装 google-cloud-storage，跳过 GCS 初始化')
        return
    try:
        info = _load_gcs_info()
        if info:
            creds = service_account.Credentials.from_service_account_info(info)
            gcs_client = storage.Client(project=info.get('project_id'), credentials=creds)
        else:
            gcs_client = storage.Client()
        gcs_client.bucket(gcs_bucket_name)
        logger.info('GCS 初始化成功 bucket=%s prefix=%s', gcs_bucket_name, gcs_prefix)
    except Exception as e:
        gcs_client = None
        logger.warning('GCS 初始化失败（将尝试 COS）: %s', e)


def _init_cos():
    global cos_client
    secret_id = getEnvConfig('TENCENT_COS_SECRET_ID')
    secret_key = getEnvConfig('TENCENT_COS_SECRET_KEY')
    if not (secret_id and secret_key):
        return
    try:
        from qcloud_cos import CosConfig, CosS3Client
        config = CosConfig(Region=cos_region, SecretId=secret_id, SecretKey=secret_key)
        cos_client = CosS3Client(config)
        logger.info('COS 客户端初始化成功')
    except Exception as e:
        cos_client = None
        logger.exception('COS 客户端初始化失败: %s', e)


_init_gcs()
_init_cos()


def _storage_ready():
    return gcs_client is not None or cos_client is not None


def _safe_rel(value):
    s = str(value or '').replace('\\', '/').strip().lstrip('/')
    if not s or '..' in s.split('/'):
        return ''
    return s


def _build_object_key(file_type, file_name, *, apply_gcs_prefix=True, basename_only=True):
    rel_type = _safe_rel(file_type)
    raw = _safe_rel(file_name)
    name = os.path.basename(raw) if basename_only else raw
    name = name or f'{uuid.uuid4().hex}'
    rel = f'{rel_type}/{name}' if rel_type else name
    if apply_gcs_prefix and gcs_prefix:
        key = f'{gcs_prefix}/{rel}'
    else:
        key = rel
    first = (key.split('/', 1)[0] or '').lower()
    if first in RESERVED_PREFIXES:
        raise ValueError('reserved object prefix')
    return key


def _public_url(key):
    if gcs_client is not None:
        return f'https://storage.googleapis.com/{gcs_bucket_name}/{key}'
    return f'https://{cos_bucket}.cos.{cos_region}.myqcloud.com/{key}'


_VIDEO_CONTENT_TYPES = {
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.mov': 'video/quicktime',
    '.webm': 'video/webm',
}
_IMAGE_CONTENT_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.avif': 'image/avif',
    '.bmp': 'image/bmp',
}


def _classify_upload(file_name, content_type=''):
    """按扩展名决定 kind 和存储用 Content-Type。`.mp4` 固定为 video/mp4。"""
    ext = os.path.splitext(file_name or '')[1].lower()
    if ext in _VIDEO_CONTENT_TYPES:
        return 'video', _VIDEO_CONTENT_TYPES[ext]
    if ext in _IMAGE_CONTENT_TYPES:
        return 'image', _IMAGE_CONTENT_TYPES[ext]
    ctype = (content_type or '').split(';', 1)[0].strip().lower()
    if ctype.startswith('video/'):
        return 'video', ctype
    if ctype.startswith('image/'):
        return 'image', ctype
    guessed = mimetypes.guess_type(file_name or '')[0] or ctype or 'application/octet-stream'
    if guessed.startswith('video/'):
        kind = 'video'
    elif guessed.startswith('image/'):
        kind = 'image'
    else:
        kind = 'file'
    return kind, guessed


def _put_object(file_content, key, content_type=''):
    last_err = None
    ctype = content_type or mimetypes.guess_type(key)[0] or 'application/octet-stream'
    if gcs_client is not None:
        bucket = gcs_client.bucket(gcs_bucket_name)
        blob = bucket.blob(key)
        for attempt in range(3):
            try:
                blob.upload_from_string(file_content, content_type=ctype)
                return True
            except Exception as e:
                last_err = e
                logger.warning('GCS upload attempt=%s err=%s', attempt + 1, e)
        raise last_err
    if cos_client is None:
        raise RuntimeError('storage not configured')
    for attempt in range(3):
        try:
            response = cos_client.put_object(
                Bucket=cos_bucket,
                Body=file_content,
                Key=key,
                StorageClass='STANDARD',
                EnableMD5=False,
            )
            if response and response.get('ETag'):
                return True
            last_err = RuntimeError('COS upload failed: empty ETag')
        except Exception as e:
            last_err = e
            logger.warning('COS put_object attempt=%s err=%s', attempt + 1, e)
    raise last_err


class IsTokenOrAdmin(BasePermission):
    """App token 或后台管理员均可上传。"""

    def has_permission(self, request, view):
        try:
            if IsTokenValid().has_permission(request, view):
                return True
        except AuthenticationFailed:
            pass
        try:
            return bool(IsAdmin().has_permission(request, view))
        except AuthenticationFailed:
            return False


@extend_schema(tags=['上传管理'])
class UploadResourceView(APIView):
    """上传资源到 GCS（回退 COS）。multipart file；可选 query type、file_name。"""

    parser_classes = (MultiPartParser, FormParser)
    permission_classes = [IsTokenOrAdmin]
    throttle_scope = 'upload'
    MAX_UPLOAD_BYTES = 15 * 1024 * 1024

    @extend_schema(
        summary='上传图片/视频等到对象存储',
        description='上传到 GCS（{GCS_BUCKET}/{GCS_PREFIX}/...），返回公网 URL',
        parameters=[
            OpenApiParameter(
                name='type', type=str, location=OpenApiParameter.QUERY,
                description='子目录，如 img、video；会拼在 GCS_PREFIX 后', required=False,
            ),
            OpenApiParameter(
                name='file_name', type=str, location=OpenApiParameter.QUERY,
                description='自定义文件名', required=False,
            ),
        ],
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'file': {'type': 'string', 'format': 'binary', 'description': '要上传的文件'},
                },
                'required': ['file'],
            }
        },
        responses={
            200: {'description': '上传成功或业务错误（看 body.code）'},
        },
    )
    def _upload_one(self, uploaded_file, file_type, custom_name=None):
        """上传单个文件，返回 {url, name, original} 或抛异常。"""
        size = int(getattr(uploaded_file, 'size', 0) or 0)
        if size > self.MAX_UPLOAD_BYTES:
            raise ValueError(f'file too large ({uploaded_file.name})')
        file_name = custom_name or uploaded_file.name
        ext = os.path.splitext(file_name)[1]
        safe_name = f'{uuid.uuid4().hex}{ext}'
        key = _build_object_key(file_type, safe_name, apply_gcs_prefix=gcs_client is not None)
        file_content = uploaded_file.read()
        kind, content_type = _classify_upload(
            file_name, getattr(uploaded_file, 'content_type', '') or '',
        )
        _put_object(file_content, key, content_type=content_type)
        return {
            'url': _public_url(key),
            'name': key,
            'original': uploaded_file.name,
            'content_type': content_type,
            'kind': kind,
        }

    def post(self, request):
        try:
            file_type = request.query_params.get('type', '')
            custom_name = (
                request.query_params.get('file_name')
                or request.POST.get('file_name')
                or None
            )
            # 先看多文件 files
            files = request.FILES.getlist('files')
            if files:
                if not _storage_ready():
                    return ApiResponse(data={}, message='storage not configured', code=500)
                results = []
                failed = []
                for f in files:
                    try:
                        results.append(self._upload_one(f, file_type))
                    except Exception as e:
                        failed.append({'original': f.name, 'error': str(e)})
                return ApiResponse(
                    data={'files': results, 'failed': failed, 'total': len(files), 'success': len(results)},
                    message='上传成功',
                    code=200,
                )
            # 回退单文件 file（向后兼容）
            uploaded_file = request.FILES.get('file')
            if not uploaded_file:
                return ApiResponse(data={}, message='未找到上传文件', code=400)
            if not _storage_ready():
                return ApiResponse(data={}, message='storage not configured', code=500)
            result = self._upload_one(uploaded_file, file_type, custom_name)
            return ApiResponse(
                data={
                    'url': result['url'],
                    'name': result['name'],
                    'content_type': result['content_type'],
                    'kind': result['kind'],
                },
                message='上传成功',
                code=200,
            )
        except ValueError as e:
            return ApiResponse(data={}, message=str(e), code=400)
        except Exception as e:
            logger.exception('upload failed')
            return ApiResponse(data={}, message=f'上传过程中发生错误: {e}', code=500)


def upload_image_to_cos(file_content, full_file_name):
    """程序内上传 bytes；优先 GCS。失败返回 None。"""
    try:
        if not _storage_ready():
            logger.error('upload_image_to_cos: storage not configured')
            return None
        use_gcs = gcs_client is not None
        rel = _safe_rel(full_file_name)
        if not rel:
            return None
        key = _build_object_key('', rel, apply_gcs_prefix=use_gcs, basename_only=False) if use_gcs else rel
        _put_object(file_content, key)
        return {'url': _public_url(key), 'name': key}
    except Exception as e:
        logger.error('上传对象存储失败: %s', e)
        return None
