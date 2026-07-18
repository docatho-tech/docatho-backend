from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from docatho_backend.masters.models import BaseModel


class NotificationType(models.TextChoices):
    ORDER_PLACED = "order_placed", _("Order placed")
    ORDER_APPROVED = "order_approved", _("Order approved")
    ORDER_REJECTED = "order_rejected", _("Order rejected")
    ORDER_PACKED = "order_packed", _("Order packed")
    OUT_FOR_DELIVERY = "out_for_delivery", _("Out for delivery")
    ORDER_DELIVERED = "order_delivered", _("Order delivered")
    ORDER_CANCELLED = "order_cancelled", _("Order cancelled")
    PAYMENT = "payment", _("Payment update")
    GENERIC = "generic", _("Notification")


class Notification(BaseModel):
    """A single in-app notification record.

    Push delivery (Firebase) is handled by a pluggable
    :class:`~docatho_backend.notifications.services.NotificationService` backend
    wired in S5; the record here is the durable source of truth regardless of
    whether a push was delivered.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(
        max_length=32,
        choices=NotificationType,
        default=NotificationType.GENERIC,
    )
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    # Soft link to the related order (string ref avoids an import cycle).
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    data = models.JSONField(blank=True, null=True)
    is_read = models.BooleanField(default=False)
    # Set once a push backend has attempted delivery.
    is_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Notification<{self.pk}> to={self.recipient_id} type={self.notification_type}"
