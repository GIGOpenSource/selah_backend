#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from rest_framework import serializers
from models.models import Example


class ExampleSerializer(serializers.ModelSerializer):
    """示例序列化器"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Example
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class ExampleCreateSerializer(serializers.ModelSerializer):
    """示例创建序列化器"""

    class Meta:
        model = Example
        fields = ['name', 'description', 'status', 'sort_order', 'remark']


class ExampleUpdateSerializer(serializers.ModelSerializer):
    """示例更新序列化器"""

    class Meta:
        model = Example
        fields = ['name', 'description', 'status', 'sort_order', 'is_deleted', 'remark']
