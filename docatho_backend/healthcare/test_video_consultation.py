from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.video import close_stale_video_consultations
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


def test_free_consultation_checkout_skips_the_gateway():
    """A ₹0 doctor is bookable: Razorpay rejects orders under ₹1."""
    patient = UserFactory()
    doctor = DoctorProfileFactory(fee_online=0, auto_accept_appointments=True)
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(minutes=10),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.PENDING,
        fee=0,
        payment_status=AppointmentPaymentStatus.PENDING,
    )
    client = APIClient()
    client.force_authenticate(user=patient)
    res = client.post(f"/api/healthcare/appointments/{appt.pk}/checkout/")
    assert res.status_code == 200
    assert res.data["razorpay_order"] is None

    appt.refresh_from_db()
    assert appt.payment_status == AppointmentPaymentStatus.PAID
    assert appt.payment_method == "free"
    # Paid and accepted means the lobby lets the patient in.
    assert appt.status == AppointmentStatus.CONFIRMED
    token = client.post(f"/api/healthcare/appointments/{appt.pk}/video-token/")
    assert token.status_code == 200


def test_paid_consultation_still_needs_a_verified_signature():
    """The free path must not become a way to skip signature checks."""
    patient = UserFactory()
    doctor = DoctorProfileFactory(fee_online=500)
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(minutes=10),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.PENDING,
        fee=500,
        payment_status=AppointmentPaymentStatus.PENDING,
    )
    client = APIClient()
    client.force_authenticate(user=patient)
    res = client.post(
        f"/api/healthcare/appointments/{appt.pk}/confirm-payment/",
        {"razorpay_order_id": "order_fake", "razorpay_payment_id": "pay_fake"},
        format="json",
    )
    assert res.status_code == 400
    appt.refresh_from_db()
    assert appt.payment_status == AppointmentPaymentStatus.PENDING


def test_doctor_leaving_the_room_completes_the_consultation():
    """Without this, an online appointment stays in_progress for good."""
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

    provider_client = APIClient()
    provider_client.force_authenticate(user=doctor.provider.user)
    join = provider_client.post(
        f"/api/healthcare/provider/appointments/{appt.pk}/video-token/",
    )
    assert join.status_code == 200
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.IN_PROGRESS

    end = provider_client.post(
        f"/api/healthcare/provider/appointments/{appt.pk}/video-end/",
    )
    assert end.status_code == 200
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.COMPLETED
    assert appt.video_ended_at is not None
    assert appt.completed_at is not None

    # Completed is what unlocks rating for the patient.
    patient_client = APIClient()
    patient_client.force_authenticate(user=patient)
    rated = patient_client.post(
        f"/api/healthcare/appointments/{appt.pk}/rate/",
        {"rating": 5, "feedback": ""},
        format="json",
    )
    assert rated.status_code == 200

    # Leaving twice must not re-complete or move a cancelled consultation.
    appt.status = AppointmentStatus.CANCELLED
    appt.save(update_fields=["status"])
    again = provider_client.post(
        f"/api/healthcare/provider/appointments/{appt.pk}/video-end/",
    )
    assert again.status_code == 200
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.CANCELLED


def test_sweep_closes_a_consultation_left_running():
    """An app killed mid-call, or a doctor who never joined."""
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    stale = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() - timedelta(hours=5),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.IN_PROGRESS,
        fee=500,
        payment_method="online",
        payment_status=AppointmentPaymentStatus.PAID,
        paid_at=timezone.now(),
        video_started_at=timezone.now() - timedelta(hours=5),
    )
    # Still inside its window: a call under way must survive the sweep.
    running = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() - timedelta(minutes=10),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.IN_PROGRESS,
        fee=500,
        payment_method="online",
        payment_status=AppointmentPaymentStatus.PAID,
        paid_at=timezone.now(),
        video_started_at=timezone.now() - timedelta(minutes=10),
    )

    assert close_stale_video_consultations() == 1

    stale.refresh_from_db()
    running.refresh_from_db()
    assert stale.status == AppointmentStatus.COMPLETED
    assert stale.video_ended_at is not None
    assert stale.completed_at is not None
    assert running.status == AppointmentStatus.IN_PROGRESS

    # Second run finds nothing left to close.
    assert close_stale_video_consultations() == 0


def test_another_doctor_cannot_end_someone_elses_consultation():
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    other = DoctorProfileFactory()
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(minutes=10),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.IN_PROGRESS,
        fee=500,
        payment_method="online",
        payment_status=AppointmentPaymentStatus.PAID,
        paid_at=timezone.now(),
    )
    client = APIClient()
    client.force_authenticate(user=other.provider.user)
    res = client.post(f"/api/healthcare/provider/appointments/{appt.pk}/video-end/")
    assert res.status_code == 404
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.IN_PROGRESS


def test_join_window_closes_after_the_appointment():
    """15 minutes before, two hours after — outside that, nobody joins."""
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() - timedelta(hours=3),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.CONFIRMED,
        fee=500,
        payment_method="online",
        payment_status=AppointmentPaymentStatus.PAID,
        paid_at=timezone.now(),
    )
    patient_client = APIClient()
    patient_client.force_authenticate(user=patient)
    assert (
        patient_client.post(
            f"/api/healthcare/appointments/{appt.pk}/video-token/",
        ).status_code
        == 403
    )

    provider_client = APIClient()
    provider_client.force_authenticate(user=doctor.provider.user)
    assert (
        provider_client.post(
            f"/api/healthcare/provider/appointments/{appt.pk}/video-token/",
        ).status_code
        == 403
    )


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


def test_unconfigured_video_503s_instead_of_minting_a_fake_token():
    """Production with no 100ms env used to hand the app a token no room
    accepts, and the failure read as "the video call is broken"."""
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
    )
    client = APIClient()
    client.force_authenticate(user=patient)

    with override_settings(HMS_ALLOW_MOCK_TOKENS=False):
        res = client.post(f"/api/healthcare/appointments/{appt.pk}/video-token/")

    assert res.status_code == 503
    assert "not configured" in str(res.data["detail"])
    # Nothing half-done: no fake room id left on the record.
    appt.refresh_from_db()
    assert appt.video_room_id == ""
