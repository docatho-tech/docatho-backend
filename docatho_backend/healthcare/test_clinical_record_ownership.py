"""Two things a client must never be able to do to someone's medical record.

A patient cannot delete their own appointment — that cascade would take the
consultation thread and the prescription with it — and no account can claim
another handset's push token and start receiving that patient's notifications.
"""

from datetime import date
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.notifications.models import DeviceToken
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_patient_cannot_delete_their_own_appointment(auth_client):
    patient = UserFactory(name="Aarav Sharma", dob=date(1994, 5, 2), gender="male")
    appointment = Appointment.objects.create(
        patient=patient,
        doctor=DoctorProfileFactory(),
        scheduled_at=timezone.now() - timedelta(hours=1),
        consultation_mode="online",
        status=AppointmentStatus.COMPLETED,
        fee=Decimal("500.00"),
    )

    response = auth_client(patient).delete(
        f"/api/healthcare/appointments/{appointment.pk}/",
    )

    assert response.status_code == 405
    assert Appointment.objects.filter(pk=appointment.pk).exists()


def test_registering_another_users_token_does_not_route_their_pushes(auth_client):
    victim = UserFactory()
    attacker = UserFactory()
    DeviceToken.objects.create(user=victim, token="handset-token", platform="android")

    response = auth_client(attacker).post(
        "/api/device-tokens/",
        {"token": "handset-token", "platform": "android"},
        format="json",
    )

    assert response.status_code in (200, 201)
    # The attacker may hold a row for the token they posted, but it must not be
    # the victim's row: nothing may still point the victim's pushes anywhere.
    assert not DeviceToken.objects.filter(user=victim, token="handset-token").exists()
    assert (
        DeviceToken.objects.filter(token="handset-token")
        .exclude(user=attacker)
        .count()
        == 0
    )
