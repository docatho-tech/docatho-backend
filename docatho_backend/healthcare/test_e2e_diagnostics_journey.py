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
    assert any(
        b["id"] == booking_id for b in admin_list.data.get("results", admin_list.data)
    )

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


def test_package_is_charged_at_its_own_price_and_never_twice(auth_client):
    """A pack sells below the sum of its parts, and an overlapping test is free.

    Charging a package as `sum(tests)` would erase the discount that is the
    reason to sell one; charging a test that the package already contains
    would bill the patient twice for one sample.
    """
    admin = AdminUserFactory()
    patient = UserFactory()
    admin_client = auth_client(admin)
    patient_client = auth_client(patient)

    lipid = DiagnosticTestFactory(price=Decimal("600.00"))
    thyroid = DiagnosticTestFactory(price=Decimal("700.00"))
    vitamin_d = DiagnosticTestFactory(price=Decimal("900.00"))

    created = admin_client.post(
        "/api/healthcare/diagnostic-packages/",
        {
            "name": "Heart & hormones",
            "test_ids": [lipid.id, thyroid.id],
            "price": "999.00",
            "mrp": "1300.00",
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    assert created.data["discount_percent"] == 23
    assert Decimal(created.data["tests_total"]) == Decimal("1300.00")

    one_test = admin_client.post(
        "/api/healthcare/diagnostic-packages/",
        {"name": "Not a pack", "test_ids": [lipid.id], "price": "100.00"},
        format="json",
    )
    assert one_test.status_code == 400

    cheap_mrp = admin_client.post(
        "/api/healthcare/diagnostic-packages/",
        {
            "name": "Backwards",
            "test_ids": [lipid.id, thyroid.id],
            "price": "999.00",
            "mrp": "500.00",
        },
        format="json",
    )
    assert cheap_mrp.status_code == 400

    booked = patient_client.post(
        "/api/healthcare/diagnostic-bookings/",
        {
            # `lipid` is already inside the package: it must not be billed again.
            "package_ids": [created.data["id"]],
            "test_ids": [lipid.id, vitamin_d.id],
            "patient_address": "12 MG Road, Bangalore",
        },
        format="json",
    )
    assert booked.status_code == 201, booked.data
    assert Decimal(booked.data["total_amount"]) == Decimal("1899.00")
    assert {t["id"] for t in booked.data["tests"]} == {
        lipid.id,
        thyroid.id,
        vitamin_d.id,
    }

    empty = patient_client.post(
        "/api/healthcare/diagnostic-bookings/",
        {"patient_address": "12 MG Road, Bangalore"},
        format="json",
    )
    assert empty.status_code == 400
