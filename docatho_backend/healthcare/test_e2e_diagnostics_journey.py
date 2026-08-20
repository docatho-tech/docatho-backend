"""Diagnostic booking lifecycle E2E across patient and admin."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import DiagnosticTestFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_diagnostic_booking_full_lifecycle(auth_client):
    patient = UserFactory()
    admin = AdminUserFactory()
    test = DiagnosticTestFactory(price=Decimal("799.00"))
    patient_client = auth_client(patient)
    admin_client = auth_client(admin)

    created = patient_client.post(
        "/api/healthcare/diagnostic-bookings/",
        {
            "test_ids": [test.id],
            "patient_address": "12 MG Road, Bangalore",
            "scheduled_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    booking_id = created.data["id"]
    assert created.data["status"] == DiagnosticBookingStatus.REQUESTED

    listed = patient_client.get("/api/healthcare/diagnostic-bookings/")
    assert any(b["id"] == booking_id for b in listed.data.get("results", listed.data))

    admin_list = admin_client.get("/api/healthcare/admin/diagnostic-bookings/")
    assert admin_list.status_code == 200
    assert any(b["id"] == booking_id for b in admin_list.data.get("results", admin_list.data))

    for status in (
        DiagnosticBookingStatus.CONFIRMED,
        DiagnosticBookingStatus.SAMPLE_COLLECTED,
        DiagnosticBookingStatus.COMPLETED,
    ):
        resp = admin_client.patch(
            f"/api/healthcare/admin/diagnostic-bookings/{booking_id}/",
            {"status": status},
            format="json",
        )
        assert resp.status_code == 200, (status, resp.data)

    assert Notification.objects.filter(
        recipient=patient,
        notification_type=NotificationType.DIAG_BOOKING_COMPLETED,
    ).exists()
