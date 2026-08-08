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
    APPOINTMENT_BOOKED = "appointment_booked", _("Appointment booked")
    APPOINTMENT_CONFIRMED = "appointment_confirmed", _("Appointment confirmed")
    APPOINTMENT_REJECTED = "appointment_rejected", _("Appointment rejected")
    APPOINTMENT_COMPLETED = "appointment_completed", _("Appointment completed")
    APPOINTMENT_CANCELLED = "appointment_cancelled", _("Appointment cancelled")
    DIAG_BOOKING_REQUESTED = "diag_booking_requested", _("Diagnostic booking requested")
    DIAG_BOOKING_CONFIRMED = "diag_booking_confirmed", _("Diagnostic booking confirmed")
    DIAG_BOOKING_CANCELLED = "diag_booking_cancelled", _("Diagnostic booking cancelled")
    DIAG_BOOKING_COMPLETED = "diag_booking_completed", _("Diagnostic booking completed")
    DIAG_SAMPLE_COLLECTED = "diag_sample_collected", _("Diagnostic sample collected")
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


class DeviceTokenPlatform(models.TextChoices):
    WEB = "web", _("Web")
    IOS = "ios", _("iOS")
    ANDROID = "android", _("Android")


class DeviceToken(BaseModel):
    """FCM device token registered by a client app for push delivery."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(
        max_length=16,
        choices=DeviceTokenPlatform.choices,
        default=DeviceTokenPlatform.WEB,
    )

    class Meta:
        indexes = [
            models.Index(fields=["user", "platform"]),
        ]

    def __str__(self) -> str:
        return f"DeviceToken<{self.pk}> user={self.user_id} platform={self.platform}"
