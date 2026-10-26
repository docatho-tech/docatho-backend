"""The provider-facing notification list the partner app's screen is built on.

Three things that screen cannot be built without: the mode filter behind its
"In clinic Visits" / "Online Consults" tabs, a reminder that fires once rather
than on every sweep, and a rating that reaches the doctor it was left for.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.reminders import send_due_reminders
from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _appointment(doctor, patient, *, mode="online", minutes=15, status=None):
    return Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(minutes=minutes),
        consultation_mode=mode,
        status=status or AppointmentStatus.CONFIRMED,
        fee=doctor.fee_online,
    )


def test_reminder_fires_once_per_appointment(auth_client):
    doctor = DoctorProfileFactory()
    _appointment(doctor, UserFactory(), minutes=15)
    # Outside the window: reminding about it now would be an hour early.
    _appointment(doctor, UserFactory(), minutes=120)

    assert send_due_reminders() == 1
    assert send_due_reminders() == 0

    reminders = Notification.objects.filter(
        notification_type=NotificationType.APPOINTMENT_REMINDER,
    )
    assert reminders.count() == 1
    assert reminders.first().recipient == doctor.provider.user


def test_notifications_filter_by_consultation_mode(auth_client):
    doctor = DoctorProfileFactory()
    provider_user = doctor.provider.user
    for mode in ("online", "in_clinic", "in_clinic"):
        Notification.objects.create(
            recipient=provider_user,
            notification_type=NotificationType.APPOINTMENT_BOOKED,
            title="New request",
            data={"appointment_id": 1, "consultation_mode": mode},
        )

    client = auth_client(provider_user)
    assert client.get("/api/notifications/").data["count"] == 3
    assert client.get("/api/notifications/?mode=online").data["count"] == 1
    assert client.get("/api/notifications/?mode=in_clinic").data["count"] == 2
    # An unknown mode is not a filter, so it must not silently empty the list.
    assert client.get("/api/notifications/?mode=nonsense").data["count"] == 3


def test_rating_notifies_the_doctor(auth_client):
    doctor = DoctorProfileFactory()
    patient = UserFactory()
    appointment = _appointment(
        doctor, patient, minutes=-60, status=AppointmentStatus.COMPLETED,
    )

    resp = auth_client(patient).post(
        f"/api/healthcare/appointments/{appointment.pk}/rate/",
        {"rating": 5},
        format="json",
    )
    assert resp.status_code == 200

    notification = Notification.objects.get(
        recipient=doctor.provider.user,
        notification_type=NotificationType.APPOINTMENT_RATED,
    )
    assert notification.data["rating"] == 5
