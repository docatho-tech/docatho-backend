"""Notification inbox and device token E2E."""

from datetime import timedelta

import pytest
from django.utils import timezone

from docatho_backend.notifications.models import Notification
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_notification_inbox_mark_read_and_unread_count(auth_client):
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    client = auth_client(patient)

    booking = client.post(
        "/api/healthcare/appointments/",
        {
            "doctor": doctor.id,
            "scheduled_at": (timezone.now() + timedelta(days=2)).isoformat(),
            "consultation_mode": "online",
            "symptoms": "Fever",
            "payment_method": "cod",
        },
        format="json",
    )
    assert booking.status_code == 201

    unread = client.get("/api/notifications/unread_count/")
    assert unread.status_code == 200
    assert unread.data["unread"] >= 1

    inbox = client.get("/api/notifications/")
    assert inbox.status_code == 200
    items = inbox.data.get("results", inbox.data)
    assert len(items) >= 1
    notif_id = items[0]["id"]

    marked = client.post(f"/api/notifications/{notif_id}/mark_read/")
    assert marked.status_code == 200

    unread_after = client.get("/api/notifications/unread_count/")
    assert unread_after.data["unread"] == max(0, unread.data["unread"] - 1)


def test_device_token_register(auth_client):
    user = UserFactory()
    client = auth_client(user)
    resp = client.post(
        "/api/device-tokens/",
        {"token": "fcm-test-token-e2e", "platform": "android"},
        format="json",
    )
    assert resp.status_code in (200, 201), resp.data
