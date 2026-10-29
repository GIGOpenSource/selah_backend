from rest_framework import serializers
from models.models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'role', 'status', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']

    def validate_username(self, value):
        queryset = User.objects.filter(username=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("用户名已存在")
        return value

    def validate_email(self, value):
        if not value:
            return value
        app_key = ''
        if self.instance is not None:
            app_key = getattr(self.instance, 'app_key', '') or ''
        queryset = User.objects.filter(email=value, app_key=app_key)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("邮箱已存在")
        return value
