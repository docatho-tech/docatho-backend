"""Admin healthcare operations E2E."""

from datetime import timedelta

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import ContentPage
from docatho_backend.healthcare.models import ContentPageType
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_admin_lists_all_appointments(auth_client):
    admin = AdminUserFactory()
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(days=1),
        consultation_mode="online",
        fee=doctor.fee_online,
    )
    resp = auth_client(admin).get("/api/healthcare/appointments/")
    assert resp.status_code == 200
    assert any(a["id"] == appt.id for a in resp.data.get("results", resp.data))


def test_admin_dashboard_extended_stats(auth_client):
    admin = AdminUserFactory()
    stats = auth_client(admin).get("/api/healthcare/admin/dashboard-stats/")
    assert stats.status_code == 200
    for key in (
        "patients_count",
        "doctors_count",
        "pending_doctor_verifications",
        "appointments_today",
        "diagnostic_bookings_count",
        "diagnostic_bookings_requested",
        "open_support_tickets",
        "appointments_pending_payment",
        "appointments_by_status",
    ):
        assert key in stats.data


def test_patient_reads_published_content_pages(auth_client):
    ContentPage.objects.create(
        page_type=ContentPageType.PRIVACY,
        title="Privacy Policy",
        body="We protect your data.",
        is_published=True,
    )
    patient = UserFactory()
    resp = auth_client(patient).get("/api/healthcare/content/?page_type=privacy")
    assert resp.status_code == 200
    results = resp.data.get("results", resp.data)
    assert any(p["title"] == "Privacy Policy" for p in results)


def test_duplicate_availability_slot_is_refused_with_400_not_500(auth_client):
    """A repeat slot must be a validation error, not an IntegrityError.

    `doctor` is injected in `perform_create` rather than declared on the
    serializer, so DRF could not build the UniqueTogetherValidator for the
    model's `unique_together` and the duplicate reached Postgres — surfacing as
    a 500 with a debug page in the body.
    """
    admin = AdminUserFactory()
    doctor = DoctorProfileFactory()
    payload = {
        "doctor": doctor.id,
        "day_of_week": 6,
        "start_time": "06:15",
        "end_time": "06:45",
        "consultation_mode": "online",
    }

    first = auth_client(admin).post("/api/healthcare/admin/availability/", payload)
    assert first.status_code == 201, first.data

    second = auth_client(admin).post("/api/healthcare/admin/availability/", payload)
    assert second.status_code == 400, second.data
    assert "start_time" in second.data
