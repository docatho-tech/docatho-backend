"""Healthcare event notifications for appointments and diagnostic bookings."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import DiagnosticBooking
from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import DiagnosticTestFactory
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_appointment_create_notifies_patient_and_provider(auth_client):
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    scheduled = timezone.now() + timedelta(days=2)
    client = auth_client(patient)

    with patch("docatho_backend.healthcare.views.notify") as mock_notify:
        resp = client.post(
            "/api/healthcare/appointments/",
            {
                "doctor": doctor.id,
                "scheduled_at": scheduled.isoformat(),
                "consultation_mode": "online",
                "symptoms": "Fever",
                "payment_method": "cod",
            },
            format="json",
        )

    assert resp.status_code == 201
    assert mock_notify.call_count == 2
    recipients = {call.args[0] for call in mock_notify.call_args_list}
    assert patient in recipients
    assert doctor.provider.user in recipients
    types = {call.args[1] for call in mock_notify.call_args_list}
    assert types == {NotificationType.APPOINTMENT_BOOKED}


def test_appointment_create_persists_notifications(auth_client):
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    scheduled = timezone.now() + timedelta(days=2)
    client = auth_client(patient)

    resp = client.post(
        "/api/healthcare/appointments/",
        {
            "doctor": doctor.id,
            "scheduled_at": scheduled.isoformat(),
            "consultation_mode": "online",
            "symptoms": "Fever",
            "payment_method": "cod",
        },
        format="json",
    )
    assert resp.status_code == 201
    assert Notification.objects.filter(recipient=patient).count() == 1
    assert Notification.objects.filter(recipient=doctor.provider.user).count() == 1


@pytest.mark.parametrize(
    "new_status,expected_type",
    [
        (AppointmentStatus.CONFIRMED, NotificationType.APPOINTMENT_CONFIRMED),
        (AppointmentStatus.REJECTED, NotificationType.APPOINTMENT_REJECTED),
        (AppointmentStatus.COMPLETED, NotificationType.APPOINTMENT_COMPLETED),
        (AppointmentStatus.CANCELLED, NotificationType.APPOINTMENT_CANCELLED),
    ],
)
def test_provider_appointment_status_notifies_patient(
    auth_client, new_status, expected_type,
):
    doctor = DoctorProfileFactory()
    patient = UserFactory()
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(days=1),
        consultation_mode="online",
        fee=doctor.fee_online,
    )
    doc_client = auth_client(doctor.provider.user)

    with patch("docatho_backend.healthcare.views.notify") as mock_notify:
        resp = doc_client.patch(
            "/api/healthcare/provider/appointments/",
            {"appointment_id": appt.id, "status": new_status},
            format="json",
        )

    assert resp.status_code == 200
    mock_notify.assert_called_once()
    call = mock_notify.call_args
    assert call.args[0] == patient
    assert call.args[1] == expected_type


def test_diagnostic_booking_create_notifies_patient(auth_client):
    patient = UserFactory()
    test = DiagnosticTestFactory(price=Decimal("550.00"))
    client = auth_client(patient)

    with patch("docatho_backend.healthcare.views.notify") as mock_notify:
        resp = client.post(
            "/api/healthcare/diagnostic-bookings/",
            {
                "test_ids": [test.id],
                "patient_address": "221B Baker Street",
                "scheduled_date": (timezone.localdate() + timedelta(days=3)).isoformat(),
            },
            format="json",
        )

    assert resp.status_code == 201
    mock_notify.assert_called_once()
    call = mock_notify.call_args
    assert call.args[0] == patient
    assert call.args[1] == NotificationType.DIAG_BOOKING_REQUESTED


def test_admin_diagnostic_booking_status_notifies_patient(auth_client):
    patient = UserFactory()
    admin = AdminUserFactory()
    test = DiagnosticTestFactory()
    booking = DiagnosticBooking.objects.create(patient=patient, total_amount=test.price)
    booking.tests.set([test])

    with patch("docatho_backend.healthcare.views.notify") as mock_notify:
        resp = auth_client(admin).patch(
            f"/api/healthcare/admin/diagnostic-bookings/{booking.id}/",
            {"status": DiagnosticBookingStatus.CONFIRMED},
            format="json",
        )

    assert resp.status_code == 200
    mock_notify.assert_called_once()
    call = mock_notify.call_args
    assert call.args[0] == patient
    assert call.args[1] == NotificationType.DIAG_BOOKING_CONFIRMED
