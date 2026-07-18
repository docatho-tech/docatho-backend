"""Notification dispatch service.

The service persists a :class:`Notification` record and then hands it to a
pluggable *backend* for out-of-band delivery (push/SMS/email). S1 ships a
console/log backend; S5 swaps in a Firebase (FCM) backend by pointing
``settings.NOTIFICATION_BACKEND`` at it — no caller changes required.

Usage (from anywhere, e.g. order state changes)::

    from docatho_backend.notifications.services import notify
    notify(user, NotificationType.ORDER_PLACED, "Order placed", body, order=order)
"""

from __future__ import annotations

import logging
from abc import ABC
from abc import abstractmethod

from django.conf import settings
from django.utils.module_loading import import_string

from .models import Notification
from .models import NotificationType

logger = logging.getLogger(__name__)


class NotificationService(ABC):
    """Interface a delivery backend must implement."""

    @abstractmethod
    def send(self, notification: Notification) -> bool:
        """Deliver the notification out-of-band. Return True on success."""
        raise NotImplementedError


class ConsoleNotificationService(NotificationService):
    """Default backend: logs the notification. No external dependency."""

    def send(self, notification: Notification) -> bool:
        logger.info(
            "[notification] to=%s type=%s title=%s",
            notification.recipient_id,
            notification.notification_type,
            notification.title,
        )
        return True


_DEFAULT_BACKEND = "docatho_backend.notifications.services.ConsoleNotificationService"


def get_backend() -> NotificationService:
    """Resolve the configured delivery backend (defaults to console)."""
    dotted = getattr(settings, "NOTIFICATION_BACKEND", _DEFAULT_BACKEND)
    backend_cls = import_string(dotted)
    return backend_cls()


def notify(
    recipient,
    notification_type: str = NotificationType.GENERIC,
    title: str = "",
    body: str = "",
    *,
    order=None,
    data: dict | None = None,
) -> Notification:
    """Persist a notification and best-effort deliver it via the backend.

    Delivery failures never propagate to the caller — the durable record is
    always created so nothing is lost if a push backend is down.
    """
    notification = Notification.objects.create(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        body=body or "",
        order=order,
        data=data,
    )
    try:
        sent = get_backend().send(notification)
        if sent:
            notification.is_sent = True
            notification.save(update_fields=["is_sent", "updated_at"])
    except Exception:  # pragma: no cover - delivery must never break the flow
        logger.exception("Notification delivery failed for id=%s", notification.pk)
    return notification
