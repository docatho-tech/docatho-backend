from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            "id",
            "notification_type",
            "title",
            "body",
            "order",
            "data",
            "is_read",
            "is_sent",
            "created_at",
        )
        read_only_fields = fields
