# UIVSBE 后端开发规范

> **UIVSBE** = **U**niversal **I**nterface & **V**ersatile **S**ervice **B**ackend **E**ngine
>
> 基于 Django 5.2 + DRF 的快速启动后端模板，本文档是 AI 辅助开发的**强制规范**。

---

## 一、通用强制总则

1. 所有代码、接口、模型、工具类、配置，**必须100%遵循本文档规范**。
2. 项目全局统一 **UTC 时间**，所有数据库时间、接口返回时间、日志时间全部使用UTC。
3. 全局启用 **logger**，禁止使用 `print`，所有关键操作、异常必须日志记录。
4. 严格分层开发，职责单一，禁止业务逻辑、鉴权、参数校验混写。

---

## 二、技术栈与依赖约束

| 组件 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 运行环境 |
| Django | 5.2.6 | Web 框架 |
| Django REST Framework | 3.16.1 | REST API |
| PostgreSQL | 14+ | 主数据库 |
| Redis | 6+ | Token 存储、缓存 |
| drf-spectacular | 0.28.0 | API 文档 |

### 依赖约束

**禁止随意引入新依赖。** 当前 `INSTALLED_APPS` 已满足需求：

```python
INSTALLED_APPS = [
    "Apps",
    "models",
    'rest_framework',
    'drf_spectacular',
    'drf_spectacular_sidecar',
]
```

如需引入新包，必须说明理由并确认无现有替代方案。

### 项目重命名

项目名称 `uivsbe` 可替换为符合业务含义的名称，需全局替换：
- 配置包目录 `uivsbe_backend/`
- settings 中的引用
- `.env` 中的 `DJANGO_SETTINGS_MODULE`

**数据库名称保持不变**，后续人工修改并执行迁移：
```bash
# 重命名后重新生成迁移（只操作 models 应用）
python manage.py makemigrations models --settings=uivsbe_backend.settings.dev
python manage.py migrate models --settings=uivsbe_backend.settings.dev
```

---

## 三、快速启动

```bash
# 1. 创建虚拟环境
python -m venv uivsbe_env

# 2. 激活（Windows）
uivsbe_env\Scripts\activate

# 3. 安装依赖（如有代理问题需绕过）
NO_PROXY="*" pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 配置数据库和 Redis

# 5. 迁移并启动（只迁移 models 应用，避免触发 Django 自带迁移）
python manage.py makemigrations models --settings=uivsbe_backend.settings.dev
python manage.py migrate models --settings=uivsbe_backend.settings.dev
python manage.py runserver 8000 --settings=uivsbe_backend.settings.dev

# 6. 访问文档
# Swagger UI: http://127.0.0.1:8000/api/docs/
# ReDoc:      http://127.0.0.1:8000/api/redoc/
```

---

## 四、项目结构

```
backend/
├── manage.py                # Django 入口
├── requirements.txt         # Python 依赖
├── .env.example             # 环境变量模板
├── .env                     # 环境变量（不提交 Git）
├── uivsbe_backend/          # Django 配置包
│   ├── settings/
│   │   ├── dev.py           # 开发环境
│   │   └── pro.py           # 生产环境
│   ├── urls.py              # 根路由
│   └── wsgi.py / asgi.py
├── Apps/                    # 业务应用
│   ├── urls.py              # 应用路由注册
│   └── views/
│       ├── user/            # 用户模块
│       ├── admin/           # 管理员模块
│       └── Example/         # 示例模块（参考模板）
├── models/                  # 数据模型（统一定义）
│   ├── apps.py
│   ├── models.py
│   └── migrations/
├── tools/                   # 工具类
│   ├── token_tools.py       # Token（Redis + AES）
│   ├── authentication.py    # DRF 认证类
│   ├── permissions.py       # DRF 权限类
│   ├── base_views.py        # BaseViewSet
│   ├── utils.py             # ApiResponse、CustomPagination
│   ├── password_hasher.py   # 密码加密
│   └── email_tools.py       # 邮件工具（暂未启用）
└── locale/                  # 国际化
```

---

## 五、API 强制规范

### 1. 统一返回结构（所有接口强制）

**普通接口：**
```json
{
  "code": 200,
  "message": "操作成功",
  "results": {}
}
```

**分页接口：**
```json
{
  "code": 200,
  "message": "success",
  "results": {
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 100,
      "total_pages": 5
    },
    "results": [...]
  }
}
```

### 2. 字段规则

- `code`：业务状态码，成功200，错误自定义
- `message`：友好提示，支持国际化
- `results`：业务数据，禁止缺失
- 分页参数：`currentPage`、`pageSize`（Query 传参）

### 3. 强制使用工具函数

```python
from tools.utils import ApiResponse, CustomPagination

# 普通返回
return ApiResponse(data={"id": 1}, message="创建成功", code=201)

# 分页返回（ViewSet 中）
class MyViewSet(BaseViewSet):
    pagination_class = CustomPagination
```

**禁止手动构造 JSON 响应。**

---

## 六、代码开发规范

### 1. 新模块标准结构

```
Apps/views/NewModule/
├── __init__.py
├── view.py          # 视图（继承 BaseViewSet）
├── urls.py          # 路由（使用 DRF Router）
└── serializers.py   # 序列化器
```

注册路由到 `Apps/urls.py`：
```python
path('new-module/', include('Apps.views.NewModule.urls'))
```

### 2. 视图规范

```python
from tools.base_views import BaseViewSet
from tools.utils import ApiResponse, CustomPagination
from drf_spectacular.utils import extend_schema, extend_schema_view
from django.utils.translation import gettext as _

@extend_schema(tags=[_("模块名称")])
@extend_schema_view(
    list=extend_schema(summary=_('获取列表')),
    create=extend_schema(summary=_('创建')),
)
class ExampleViewSet(BaseViewSet):
    queryset = Example.objects.filter(is_deleted=False)
    serializer_class = ExampleSerializer
    pagination_class = CustomPagination
```

### 2.1 独立接口场景（CBV / FBV 均可）

当 ViewSet 无法满足需求时，可使用独立的 CBV 或 FBV：

**CBV 方式（as_view）：**

```python
from rest_framework.views import APIView
from tools.utils import ApiResponse

class UploadResourceView(APIView):
    """文件上传接口 — 独立 CBV，不走 Router"""
    @extend_schema(tags=[_("资源管理")], summary=_("上传资源"))
    def post(self, request):
        # 处理上传逻辑
        return ApiResponse(data={"url": file_url}, message="上传成功")
```

```python
# urls.py 中注册
path('upload/', UploadResourceView.as_view(), name='upload_resource'),
```

**FBV 方式（函数视图）：**

```python
from rest_framework.decorators import api_view

@extend_schema(tags=[_("资源管理")], summary=_("特殊接口"))
@api_view(['POST'])
def special_action(request):
    """复杂业务逻辑，不适合 CBV 时使用 FBV"""
    return ApiResponse(data={...}, message="操作成功")
```

```python
# urls.py 中注册
path('special/', special_action),
```

### 3. 模型规范

```python
class Example(models.Model):
    name = models.CharField(max_length=100, verbose_name="名称")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 't_example'  # 强制：表名以 t_ 开头
        verbose_name = "示例"
```

**所有新表必须以 `t_` 开头。**

### 4. 认证与权限

```python
from tools.authentication import TokenAuthentication
from tools.permissions import IsTokenValid, IsAdmin

class SecureViewSet(BaseViewSet):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsTokenValid]
```

- 公开接口：不设置 `permission_classes`
- 用户接口：`[IsTokenValid]`
- 管理接口：`[IsTokenValid, IsAdmin]`

**禁止引入 `django.contrib.auth`、SessionAuthentication、JWT。**

---

## 七、Swagger 文档规范

- 每个 ViewSet 必须加 `@extend_schema(tags=[...])` 分组
- 每个接口必须有 `summary` 说明
- 入参出参字段必须有 `help_text` 或 `verbose_name`
- 禁止空注释、缺失字段说明

---

## 八、日志规范

- 全局使用 `logging.getLogger(__name__)`，**禁用 print**
- 分级：DEBUG / INFO / WARNING / ERROR / CRITICAL
- 所有接口入参、异常堆栈必须日志记录

```python
import logging
logger = logging.getLogger(__name__)

logger.info("用户注册: %s", username)
logger.error("创建失败", exc_info=True)
```

---

## 九、国际化（i18n）

### 1. 安装 gettext

下载地址：https://mlocati.github.io/articles/gettext-iconv-windows.html

解压后将 `bin` 目录加入系统 PATH，验证：
```bash
msgfmt --version
```

### 2. 标记可翻译字符串

```python
from django.utils.translation import gettext_lazy as _

class Example(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("名称"))
```

### 3. 翻译流程

```bash
# 步骤 1：标记 → 生成 .po 文件
django-admin makemessages --all

# 步骤 2：翻译（使用项目内置百度翻译接口）
python tools/translate_po.py

# 步骤 3：编译 .po → .mo
django-admin compilemessages
```

### 4. 单语言操作

```bash
# 仅生成/编译某种语言
python manage.py makemessages -l de    # 德语
python manage.py makemessages -l fr    # 法语
django-admin compilemessages -l ja     # 日语
```

---

## 十、异常处理规范

```python
# ✅ 正确
try:
    user = User.objects.get(id=user_id)
except User.DoesNotExist:
    return ApiResponse(message="用户不存在", code=404)
except Exception as e:
    logger.error("查询用户失败", exc_info=True)
    return ApiResponse(message="服务器错误", code=500)

# ❌ 禁止
try:
    ...
except:
    pass
```

所有三方请求、IO 操作必须 try-except，禁止异常冒泡导致服务崩溃。

---

## 十一、环境配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `POSTGRES_HOST` | PostgreSQL 主机 | - |
| `POSTGRES_DB` | 数据库名称 | `uivsbe` |
| `POSTGRES_USER` | 数据库用户 | - |
| `POSTGRES_PASSWORD` | 数据库密码 | - |
| `POSTGRES_PORT` | 数据库端口 | `5433` |
| `REDIS_HOST` | Redis 主机（优先） | `localhost` |
| `REDIS_PORT` | Redis 端口 | `6389` |
| `REDIS_PASSWORD` | Redis 密码 | - |
| `REDIS_BUILDMART_*` | 历史兼容别名 | 同上 |
| `TOKEN_AES_KEY` | Token AES 密钥（约 16 字节） | 开发占位 |

---

## 十二、错误码

| 错误码 | 说明 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 400 | 请求参数错误 |
| 401 | 未授权 |
| 403 | 禁止访问 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

---

## 十三、现有接口（Selah 1.0）

详情见同目录 `API.md`。业务面仅挂载：

| 模块 | 前缀 | 说明 |
|------|------|------|
| 认证 | `/api/auth/` | 设备登录 / OAuth 绑定 / me / profile |
| 内容 | `/api/content/` | Feed / 互动 / 话题壁纸 / 资产 / app-config |
| Onboarding | `/api/content/`（onboarding 子路径） | 题目 / 完成 / 跳过 |
| 打卡 | `/api/checkin/` | status / do / prefs |
| AI | `/api/ai/` | chat / history / quick-prompts |
| 会员 | `/api/membership/` | plans / verify / restore |
| 运营 | `/api/admin-ops/` | 配置与内容运营 |
| 法务占位 | `/legal/<slug>/` | privacy / terms / help / download |
| 健康检查 | `/health`、`/api/health/` | DB + Redis |

模板遗留 `/api/users/`、`/api/admin/`、`/api/example/` **已从路由卸载**（`Apps/views/` 代码可作参考，不交付）。

---

## 十四、开发流程

```
1. 创建模块目录 Apps/views/NewModule/
2. 在 models/models.py 添加模型（表名 t_ 开头）
3. makemigrations models → migrate models（只操作 models 应用）
4. 编写 serializers.py
5. 编写 view.py（继承 BaseViewSet，加 @extend_schema）
6. 编写 urls.py，注册到 Apps/urls.py
7. python manage.py check 验证
8. 启动测试 → 提交代码
```

---

## 十五、提交规范

```
✅ 提交前：python manage.py check
✅ 提交信息：[模块] 操作描述
❌ 禁止提交：.env、__pycache__/、uivsbe_env/、db.sqlite3
```

---

> 📝 最后更新: 2026-08-06
>
> 🤖 本文档是 AI 辅助开发的强制规范，任何 AI 工具修改此项目时必须遵守。
