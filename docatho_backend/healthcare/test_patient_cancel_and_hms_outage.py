"""The patient's cancel, and a 100ms outage.

Both used to be silent in their own way: a cancelled slot stayed blocked in the
doctor's day because nothing told them, and a 100ms outage escaped as a bare 500
that the app renders as "try again closer to your appointment time" — to a
patient who is already at it.
"""

from datetime import timedelta

import pytest
import requests
from django.utils import timezone
from rest_framework.test import APIClient

from docatho_backend.healthcare.hms import HMSUnavailableError
from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _appointment(**kwargs):
    doctor = kwargs.pop("doctor", None) or DoctorProfileFactory()
    defaults = {
        "patient": UserFactory(),
        "doctor": doctor,
        "scheduled_at": timezone.now() + timedelta(hours=3),
        "consultation_mode": ConsultationMode.ONLINE,
        "status": AppointmentStatus.CONFIRMED,
        "fee": 500,
        "payment_status": AppointmentPaymentStatus.PAID,
    }
    return Appointment.objects.create(**{**defaults, **kwargs})


def test_patient_cancel_records_the_reason_and_tells_the_doctor():
    appt = _appointment()
    client = APIClient()
    client.force_authenticate(user=appt.patient)

    res = client.post(
        f"/api/healthcare/appointments/{appt.pk}/cancel/",
        {"reason": "Travelling that day"},
        format="json",
    )
    assert res.status_code == 200

    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.CANCELLED
    assert appt.cancellation_reason == "Travelling that day"

    provider_user = appt.doctor.provider.user
    note = Notification.objects.get(
        recipient=provider_user,
        notification_type=NotificationType.APPOINTMENT_CANCELLED,
    )
    assert "Travelling that day" in note.body


def test_cancel_is_refused_once_the_consultation_is_live():
    appt = _appointment(status=AppointmentStatus.IN_PROGRESS)
    client = APIClient()
    client.force_authenticate(user=appt.patient)

    res = client.post(f"/api/healthcare/appointments/{appt.pk}/cancel/")
    assert res.status_code == 400

    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.IN_PROGRESS


def test_a_100ms_outage_is_a_503_not_a_500(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectTimeout("100ms is down")

    monkeypatch.setattr(requests, "post", boom)

    from docatho_backend.healthcare.hms import HMSClient

    client = HMSClient(access_key="k", secret="s", template_id="t")
    with pytest.raises(HMSUnavailableError) as excinfo:
        client.create_room("docatho-appt-1")
    assert excinfo.value.status_code == 503
