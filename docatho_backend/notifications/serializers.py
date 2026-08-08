from rest_framework import serializers

from .models import DeviceToken
from .models import DeviceTokenPlatform
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


class DeviceTokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512)
    platform = serializers.ChoiceField(
        choices=DeviceTokenPlatform.choices,
        default=DeviceTokenPlatform.WEB,
        required=False,
    )

    def create(self, validated_data):
        user = self.context["request"].user
        token, _created = DeviceToken.objects.update_or_create(
            token=validated_data["token"],
            defaults={
                "user": user,
                "platform": validated_data.get("platform", DeviceTokenPlatform.WEB),
            },
        )
        return token
