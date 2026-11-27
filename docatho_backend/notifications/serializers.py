from django.db import transaction
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
        value = validated_data["token"]
        with transaction.atomic():
            # `token` is unique, so another account holding this token has to
            # be cleared before we can claim it. That row is stale — the
            # handset was handed over or someone else logged in on it — but it
            # is deleted rather than reassigned: keying on the token alone let
            # any caller point someone else's device at their own account and
            # receive that patient's appointment and consultation pushes.
            DeviceToken.objects.filter(token=value).exclude(user=user).delete()
            token, _created = DeviceToken.objects.update_or_create(
                user=user,
                token=value,
                defaults={
                    "platform": validated_data.get(
                        "platform", DeviceTokenPlatform.WEB,
                    ),
                },
            )
        return token
