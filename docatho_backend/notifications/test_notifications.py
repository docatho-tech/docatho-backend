"""Notification records, dispatch service, and the read/list API."""

import pytest

from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType
from docatho_backend.notifications.services import notify
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_notify_persists_and_marks_sent():
    user = UserFactory()
    n = notify(user, NotificationType.ORDER_PLACED, "Placed", "Body")
    assert Notification.objects.filter(pk=n.pk).exists()
    n.refresh_from_db()
    assert n.is_sent is True  # console backend "delivers"


def test_delivery_failure_does_not_break_caller(settings):
    settings.NOTIFICATION_BACKEND = (
        "docatho_backend.notifications.test_notifications.BrokenBackend"
    )
    user = UserFactory()
    n = notify(user, NotificationType.GENERIC, "Still works")
    assert Notification.objects.filter(pk=n.pk).exists()
    n.refresh_from_db()
    assert n.is_sent is False  # delivery failed but record persists


def test_list_only_own_notifications(auth_client):
    me = UserFactory()
    other = UserFactory()
    notify(me, NotificationType.GENERIC, "mine")
    notify(other, NotificationType.GENERIC, "theirs")
    resp = auth_client(me).get("/api/notifications/")
    results = resp.data["results"] if "results" in resp.data else resp.data
    assert len(results) == 1
    assert results[0]["title"] == "mine"


def test_unread_count_and_mark_read(auth_client):
    user = UserFactory()
    n = notify(user, NotificationType.GENERIC, "unread")
    client = auth_client(user)
    assert client.get("/api/notifications/unread_count/").data["unread"] == 1

    client.post(f"/api/notifications/{n.pk}/mark_read/")
    assert client.get("/api/notifications/unread_count/").data["unread"] == 0


def test_mark_all_read(auth_client):
    user = UserFactory()
    notify(user, NotificationType.GENERIC, "a")
    notify(user, NotificationType.GENERIC, "b")
    client = auth_client(user)
    resp = client.post("/api/notifications/mark_all_read/")
    assert resp.data["marked_read"] == 2
    assert client.get("/api/notifications/unread_count/").data["unread"] == 0


class BrokenBackend:
    """Test double whose delivery always raises."""

    def send(self, notification):
        raise RuntimeError("push provider down")


def test_register_device_token(auth_client):
    user = UserFactory()
    client = auth_client(user)
    resp = client.post(
        "/api/device-tokens/",
        {"token": "fcm-token-abc", "platform": "web"},
        format="json",
    )
    assert resp.status_code == 201
    assert resp.data["token"] == "fcm-token-abc"
    assert resp.data["platform"] == "web"

    # Re-registering the same token updates ownership if needed.
    resp2 = client.post(
        "/api/device-tokens/",
        {"token": "fcm-token-abc", "platform": "web"},
        format="json",
    )
    assert resp2.status_code == 201
    assert resp2.data["id"] == resp.data["id"]
