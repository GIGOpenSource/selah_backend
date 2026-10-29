# 在 utils.py 或单独的 base_views.py 中定义
"""
ModelViewSet 基类：CRUD 统一包装为 ApiResponse（HTTP 状态恒 200，业务码在 body.code）。

子类只需配置 queryset / serializer_class 等；异常转为 code + message。
成功码：list/retrieve/update 多为 200；create 为 201；校验失败 400；不存在 404；其它 500。
"""
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from django.core.exceptions import ObjectDoesNotExist
from tools.utils import ApiResponse  # 导入你的 ApiResponse


class BaseViewSet(viewsets.ModelViewSet):
    """基础 ViewSet，统一处理响应格式为 ApiResponse。"""
    def list(self, request, *args, **kwargs):
        """列表；有分页时走 get_paginated_response（通常已是 ApiResponse）。"""
        try:
            queryset = self.filter_queryset(self.get_queryset())
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = self.get_serializer(queryset, many=True)
            return ApiResponse(code=200, data=serializer.data, message="列表获取成功")
        except Exception as e:
            return ApiResponse(code=500, message=f"列表获取失败: {str(e)}")
    def retrieve(self, request, *args, **kwargs):
        """详情；对象不存在 → code=404。"""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return ApiResponse(data=serializer.data, message="详情获取成功")
        except ObjectDoesNotExist:
            return ApiResponse(code=404, message=f"{self.queryset.model.__name__}不存在")
        except Exception as e:
            return ApiResponse(code=500, message=f"详情获取失败: {str(e)}")
    def create(self, request, *args, **kwargs):
        """创建；校验失败 code=400，成功 code=201。"""
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            return ApiResponse(
                data=serializer.data,
                message="创建成功",
                code=201
            )
        except ValidationError as e:
            return ApiResponse(code=400, message=str(e.detail))
        except Exception as e:
            return ApiResponse(code=500, message=f"创建失败: {str(e)}")

    def update(self, request, *args, **kwargs):
        """全量更新（PUT）；校验失败时 message 可能为 detail 结构。"""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return ApiResponse(data=serializer.data, message="更新成功")
        except ObjectDoesNotExist:
            return ApiResponse(code=404, message=f"{self.queryset.model.__name__}不存在")
        except ValidationError as e:
            return ApiResponse(code=400, message=e.detail)
        except Exception as e:
            return ApiResponse(code=500, message=f"更新失败: {str(e)}")

    def partial_update(self, request, *args, **kwargs):
        """部分更新（PATCH）；partial=True 仅校验提交字段。"""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return ApiResponse(data=serializer.data, message="部分更新成功")
        except ObjectDoesNotExist:
            return ApiResponse(code=404, message=f"{self.queryset.model.__name__}不存在")
        except ValidationError as e:
            return ApiResponse(code=400, message=e.detail)
        except Exception as e:
            return ApiResponse(code=500, message=f"部分更新失败: {str(e)}")

    def destroy(self, request, *args, **kwargs):
        """删除；成功 results 默认为 {}。"""
        try:
            instance = self.get_object()
            self.perform_destroy(instance)
            return ApiResponse(message="删除成功")
        except ObjectDoesNotExist:
            return ApiResponse(code=404, message=f"{self.queryset.model.__name__}不存在")
        except Exception as e:
            return ApiResponse(code=500, message=f"删除失败: {str(e)}")
