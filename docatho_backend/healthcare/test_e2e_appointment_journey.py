"""Full appointment lifecycle E2E: book → pay → video → complete → rate."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType
from docatho_backend.testing.e2e_helpers import mock_razorpay_order
from docatho_backend.testing.e2e_helpers import patch_appointment_razorpay
from docatho_backend.testing.e2e_helpers import razorpay_signature
from docatho_backend.testing.e2e_helpers import RAZORPAY_TEST_SECRET
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db

RP_ORDER_ID = "order_appt_e2e"
RP_PAYMENT_ID = "pay_appt_e2e"


def _book_online(patient_client, doctor, scheduled_at):
    return patient_client.post(
        "/api/healthcare/appointments/",
        {
            "doctor": doctor.id,
            "scheduled_at": scheduled_at.isoformat(),
            "consultation_mode": "online",
            "symptoms": "Fever and headache",
            "payment_method": "online",
        },
        format="json",
    )


def _pay_appointment(patient_client, appt_id, fee: Decimal):
    rp_order = mock_razorpay_order(RP_ORDER_ID, fee)
    with patch_appointment_razorpay(rp_order):
        checkout = patient_client.post(
            f"/api/healthcare/appointments/{appt_id}/checkout/"
        )
    assert checkout.status_code == 200, checkout.data

    confirm = patient_client.post(
        f"/api/healthcare/appointments/{appt_id}/confirm-payment/",
        {
            "razorpay_order_id": RP_ORDER_ID,
            "razorpay_payment_id": RP_PAYMENT_ID,
            "razorpay_signature": razorpay_signature(RP_ORDER_ID, RP_PAYMENT_ID),
        },
        format="json",
    )
    assert confirm.status_code == 200, confirm.data
    assert confirm.data["payment_status"] == AppointmentPaymentStatus.PAID
    return confirm


def test_full_online_appointment_journey_book_pay_video_complete_rate(
    auth_client, settings
):
    settings.RAZORPAY_KEY_SECRET = RAZORPAY_TEST_SECRET
    settings.RAZORPAY_KEY_ID = "rzp_test_id"

    patient = UserFactory()
    doctor = DoctorProfileFactory()
    patient_client = auth_client(patient)
    doc_client = auth_client(doctor.provider.user)

    booking = _book_online(patient_client, doctor, timezone.now() + timedelta(days=2))
    assert booking.status_code == 201, booking.data
    appt_id = booking.data["id"]

    _pay_appointment(patient_client, appt_id, doctor.fee_online)

    confirmed = doc_client.patch(
        "/api/healthcare/provider/appointments/",
        {"appointment_id": appt_id, "status": AppointmentStatus.CONFIRMED},
        format="json",
    )
    assert confirmed.status_code == 200

    Appointment.objects.filter(pk=appt_id).update(
        scheduled_at=timezone.now() + timedelta(minutes=5),
    )

    patient_token = patient_client.post(
        f"/api/healthcare/appointments/{appt_id}/video-token/"
    )
    assert patient_token.status_code == 200, patient_token.data
    assert patient_token.data["auth_token"]

    doctor_token = doc_client.post(
        f"/api/healthcare/provider/appointments/{appt_id}/video-token/",
    )
    assert doctor_token.status_code == 200

    completed = doc_client.patch(
        "/api/healthcare/provider/appointments/",
        {
            "appointment_id": appt_id,
            "status": AppointmentStatus.COMPLETED,
            "prescription_notes": "Paracetamol 500mg, 1-0-1 for 3 days",
        },
        format="json",
    )
    assert completed.status_code == 200

    detail = patient_client.get(f"/api/healthcare/appointments/{appt_id}/")
    assert detail.status_code == 200
    assert detail.data["prescription_notes"]

    rated = patient_client.post(
        f"/api/healthcare/appointments/{appt_id}/rate/",
        {"rating": 5, "feedback": "Great consultation"},
        format="json",
    )
    assert rated.status_code == 200
    assert rated.data["patient_rating"] == 5

    assert Notification.objects.filter(
        recipient=patient,
        notification_type=NotificationType.APPOINTMENT_COMPLETED,
    ).exists()


def test_patient_cancels_unpaid_appointment(auth_client):
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    client = auth_client(patient)

    booking = _book_online(client, doctor, timezone.now() + timedelta(days=1))
    appt_id = booking.data["id"]

    cancelled = client.post(f"/api/healthcare/appointments/{appt_id}/cancel/")
    assert cancelled.status_code == 200
    assert cancelled.data["status"] == AppointmentStatus.CANCELLED


def test_doctor_rejects_appointment(auth_client):
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(days=1),
        consultation_mode="online",
        fee=doctor.fee_online,
        symptoms="Cough",
    )
    rejected = auth_client(doctor.provider.user).patch(
        "/api/healthcare/provider/appointments/",
        {"appointment_id": appt.id, "status": AppointmentStatus.REJECTED},
        format="json",
    )
    assert rejected.status_code == 200
    assert Notification.objects.filter(
        recipient=patient,
        notification_type=NotificationType.APPOINTMENT_REJECTED,
    ).exists()


def test_video_token_blocked_before_payment(auth_client):
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(minutes=5),
        consultation_mode="online",
        status=AppointmentStatus.CONFIRMED,
        fee=doctor.fee_online,
        payment_status=AppointmentPaymentStatus.PENDING,
    )
    res = auth_client(patient).post(
        f"/api/healthcare/appointments/{appt.id}/video-token/"
    )
    assert res.status_code == 403
