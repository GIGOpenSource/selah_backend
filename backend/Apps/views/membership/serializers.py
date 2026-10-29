#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""会员 IAP 序列化器。

字段用途：
- IapVerify：验单开通；sandbox 可无真收据；pay_trigger_source 供归因。
- IapRestore：transaction_id 或 original_transaction_id 至少一个。
"""
from rest_framework import serializers


class IapVerifySerializer(serializers.Serializer):
    """IAP 验单开通/续费。

    Fields:
        platform / product_id: 解析 MembershipProduct。
        transaction_id: 全局唯一；同用户 active 幂等。
        receipt_data: 正式环境必填；sandbox 可空（服务端造伪收据）。
        is_trial: 用试用天数而非 duration_days。
        sandbox: 开发旁路（禁止用于生产发权）。
        pay_trigger_source: 客户端付费入口归因，原样回传。
    """
    platform = serializers.ChoiceField(choices=['ios', 'android'])
    product_id = serializers.CharField(max_length=128)
    transaction_id = serializers.CharField(max_length=256)
    receipt_data = serializers.CharField(required=False, allow_blank=True, default='')
    original_transaction_id = serializers.CharField(required=False, allow_blank=True, max_length=256)
    is_trial = serializers.BooleanField(required=False, default=False)
    sandbox = serializers.BooleanField(required=False, default=False)
    pay_trigger_source = serializers.CharField(required=False, allow_blank=True, default='other')


class IapRestoreSerializer(serializers.Serializer):
    """恢复购买：按交易号找回未过期收据并挂到当前用户。"""
    transaction_id = serializers.CharField(required=False, allow_blank=True, max_length=256)
    original_transaction_id = serializers.CharField(required=False, allow_blank=True, max_length=256)

    def validate(self, attrs):
        if not (attrs.get('transaction_id') or attrs.get('original_transaction_id')):
            raise serializers.ValidationError('transaction_id or original_transaction_id required')
        return attrs
