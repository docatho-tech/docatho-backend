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

import requests
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


#: Expo's push gateway. It relays to FCM/APNs using the credentials held by the
#: Expo project, which is why this backend needs no service-account file on the
#: server — the only secret involved is optional (``EXPO_ACCESS_TOKEN``).
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

#: Expo issues tokens in these two shapes. A *native* FCM/APNs token is not
#: interchangeable with them: the gateway rejects it. The apps used to register
#: native tokens, so rows predating that change are skipped rather than sent and
#: silently dropped.
_EXPO_TOKEN_PREFIXES = ("ExponentPushToken[", "ExpoPushToken[")


class ExpoPushNotificationService(NotificationService):
    """Deliver through Expo's push gateway to every device the user registered.

    Returns True only if the gateway accepted at least one ticket, so a user
    whose every token has gone stale is not recorded as notified.
    """

    def send(self, notification: Notification) -> bool:
        # Imported here: the module is loaded by settings-time code paths that
        # must not pull in models.
        from .models import DeviceToken

        tokens = [
            token
            for token in DeviceToken.objects.filter(
                user_id=notification.recipient_id,
            ).values_list("token", flat=True)
            if token.startswith(_EXPO_TOKEN_PREFIXES)
        ]
        if not tokens:
            logger.info(
                "[notification] no Expo token for user=%s, id=%s not pushed",
                notification.recipient_id,
                notification.pk,
            )
            return False

        # `data` is what the app routes on when the notification is tapped, and
        # the type has to travel with it — the payload itself only carries the
        # ids of whatever the notification is about.
        data = dict(notification.data or {})
        data["type"] = notification.notification_type
        if notification.order_id:
            data.setdefault("order_id", notification.order_id)

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        access_token = getattr(settings, "EXPO_ACCESS_TOKEN", "")
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        messages = [
            {
                "to": token,
                "title": notification.title,
                "body": notification.body,
                "data": data,
                "sound": "default",
                "priority": "high",
                # Android needs a channel the app has created, or the
                # notification arrives with no sound and no heads-up banner.
                "channelId": "default",
            }
            for token in tokens
        ]

        response = requests.post(
            EXPO_PUSH_URL,
            json=messages,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        tickets = response.json().get("data") or []
        delivered = 0
        for ticket, token in zip(tickets, tokens, strict=False):
            if ticket.get("status") == "ok":
                delivered += 1
                continue
            details = (ticket.get("details") or {}).get("error")
            logger.warning(
                "[notification] Expo rejected id=%s: %s (%s)",
                notification.pk,
                ticket.get("message"),
                details,
            )
            # The device uninstalled or reinstalled the app. Keeping the row
            # means every later notification pays for a ticket that can only
            # fail, so the token goes.
            if details == "DeviceNotRegistered":
                DeviceToken.objects.filter(token=token).delete()
        return delivered > 0


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
