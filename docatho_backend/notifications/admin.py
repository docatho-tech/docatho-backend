from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recipient",
        "notification_type",
        "title",
        "is_read",
        "is_sent",
        "created_at",
    )
    list_filter = ("notification_type", "is_read", "is_sent")
    search_fields = ("title", "body", "recipient__phone", "recipient__name")
