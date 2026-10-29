#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""AI 对话序列化器。

Fields:
    AiChatSerializer.content: 用户输入，最长 4000；空串在 view 再拦一层。
    AiDeleteMessageSerializer.id: 消息主键，删除时校验归属本人。
"""
from rest_framework import serializers


class AiChatSerializer(serializers.Serializer):
    """发送 AI 消息入参。"""
    content = serializers.CharField(max_length=4000, help_text='用户输入')


class AiDeleteMessageSerializer(serializers.Serializer):
    """删除本人消息入参。"""
    id = serializers.IntegerField(help_text='消息 ID')
