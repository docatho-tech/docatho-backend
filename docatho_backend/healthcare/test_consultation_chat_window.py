"""The consultation thread's write gate, which only the client used to have.

`chat.tsx` checked the window off the device clock and the endpoint checked
nothing, so a patient could message on a pending, rejected, cancelled or unpaid
appointment and forever after it finished — each message pushing the doctor.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _appointment(**overrides):
    defaults = {
        "patient": UserFactory(),
        "doctor": DoctorProfileFactory(),
        "scheduled_at": timezone.now(),
        "consultation_mode": "online",
        "fee": Decimal("500.00"),
        "payment_method": "online",
        "payment_status": AppointmentPaymentStatus.PAID,
        "status": AppointmentStatus.CONFIRMED,
    }
    return Appointment.objects.create(**(defaults | overrides))


def _send(auth_client, appointment):
    return auth_client(appointment.patient).post(
        f"/api/healthcare/appointments/{appointment.pk}/messages/",
        {"body": "Doctor, one more question"},
        format="json",
    )


def test_patient_can_message_inside_the_window(auth_client):
    appointment = _appointment()
    assert _send(auth_client, appointment).status_code == 201


def test_a_free_consultation_is_not_shut_out(auth_client):
    """Zero-fee bookings are marked paid without the gateway."""
    appointment = _appointment(fee=Decimal("0.00"), payment_method="free")
    assert _send(auth_client, appointment).status_code == 201


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("pending", {"status": AppointmentStatus.PENDING}),
        ("rejected", {"status": AppointmentStatus.REJECTED}),
        ("cancelled", {"status": AppointmentStatus.CANCELLED}),
        ("unpaid", {"payment_status": AppointmentPaymentStatus.PENDING}),
        ("too early", {"scheduled_at": timezone.now() + timedelta(hours=3)}),
        (
            "long finished",
            {
                "status": AppointmentStatus.COMPLETED,
                "completed_at": timezone.now() - timedelta(days=3),
                "scheduled_at": timezone.now() - timedelta(days=3),
            },
        ),
    ],
)
def test_closed_threads_reject_the_message(auth_client, label, overrides):
    appointment = _appointment(**overrides)
    resp = _send(auth_client, appointment)
    assert resp.status_code == 403, f"{label}: {resp.status_code}"
    assert appointment.messages.count() == 0


def test_a_just_finished_consultation_still_takes_a_follow_up(auth_client):
    appointment = _appointment(
        status=AppointmentStatus.COMPLETED,
        completed_at=timezone.now() - timedelta(hours=1),
    )
    assert _send(auth_client, appointment).status_code == 201


def test_reading_a_closed_thread_is_still_allowed(auth_client):
    appointment = _appointment(status=AppointmentStatus.CANCELLED)
    resp = auth_client(appointment.patient).get(
        f"/api/healthcare/appointments/{appointment.pk}/messages/",
    )
    assert resp.status_code == 200
