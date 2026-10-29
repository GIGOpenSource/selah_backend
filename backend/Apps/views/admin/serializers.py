#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from rest_framework import serializers
from models.models import User
from django.utils.translation import gettext as _


class AdminRegisterSerializer(serializers.ModelSerializer):
    """管理员注册序列化器"""
    password = serializers.CharField(write_only=True, min_length=6)
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'password', 'confirm_password')

    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError(_("两次输入的密码不一致"))
        return data

    def create(self, validated_data):
        validated_data.pop('confirm_password')
        validated_data['role'] = 'admin'
        user = User.objects.create(**validated_data)
        return user


class AdminLoginSerializer(serializers.Serializer):
    """管理员登录序列化器"""
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class AdminUserSerializer(serializers.ModelSerializer):
    """管理员信息序列化器"""
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'role', 'status', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')
