from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_video_token_requires_payment():
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(minutes=10),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.CONFIRMED,
        fee=500,
        payment_method="online",
        payment_status=AppointmentPaymentStatus.PENDING,
    )
    client = APIClient()
    client.force_authenticate(user=patient)
    res = client.post(f"/api/healthcare/appointments/{appt.pk}/video-token/")
    assert res.status_code == 403


def test_video_token_after_payment():
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(minutes=10),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.CONFIRMED,
        fee=500,
        payment_method="online",
        payment_status=AppointmentPaymentStatus.PAID,
        paid_at=timezone.now(),
    )
    client = APIClient()
    client.force_authenticate(user=patient)
    res = client.post(f"/api/healthcare/appointments/{appt.pk}/video-token/")
    assert res.status_code == 200
    assert "auth_token" in res.data
    assert res.data["appointment_id"] == appt.pk
