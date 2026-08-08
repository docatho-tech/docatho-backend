"""Razorpay checkout for healthcare appointments."""

from __future__ import annotations

from typing import Any

import requests
from django.db import transaction
from django.utils import timezone

from docatho_backend.orders.razorpay import RazorpayClient

from .models import Appointment
from .models import AppointmentPaymentStatus
from .models import AppointmentPaymentTransaction
from .models import AppointmentStatus

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


def create_appointment_checkout(appointment: Appointment) -> dict[str, Any]:
    if appointment.consultation_mode != "online":
        raise ValueError("Only online consultations require prepayment")
    if appointment.payment_status == AppointmentPaymentStatus.PAID:
        raise ValueError("Appointment already paid")

    amount_paisa = int((appointment.fee * 100).to_integral_value())
    client = RazorpayClient()
    payload = {
        "amount": amount_paisa,
        "currency": "INR",
        "receipt": f"appt-{appointment.pk}",
        "payment_capture": 1,
        "notes": {"appointment_id": str(appointment.pk), "patient_id": str(appointment.patient_id)},
    }
    resp = requests.post(f"{RAZORPAY_API_BASE}/orders", auth=client._auth(), json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    AppointmentPaymentTransaction.objects.create(
        appointment=appointment,
        provider="razorpay",
        transaction_order_id=data.get("id"),
        amount=appointment.fee,
        succeeded=False,
        raw_response=data,
    )
    appointment.payment_method = "online"
    appointment.save(update_fields=["payment_method", "updated_at"])
    return data


@transaction.atomic
def confirm_appointment_payment(
    appointment: Appointment,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str | None = None,
    raw_response: dict[str, Any] | None = None,
) -> AppointmentPaymentTransaction:
    client = RazorpayClient()
    if razorpay_signature:
        ok = client.verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature)
        if not ok:
            raise ValueError("Invalid signature")

    tr = (
        AppointmentPaymentTransaction.objects.select_for_update()
        .filter(appointment=appointment, transaction_order_id=razorpay_order_id)
        .first()
    )
    if tr is None:
        raise ValueError("No payment transaction found for this appointment")

    tr.razorpay_payment_id = razorpay_payment_id
    tr.razorpay_signature = razorpay_signature or ""
    tr.succeeded = True
    tr.paid_at = timezone.now()
    if raw_response is not None:
        tr.raw_response = raw_response
    tr.save()

    appointment.payment_status = AppointmentPaymentStatus.PAID
    appointment.paid_at = timezone.now()
    if appointment.doctor.auto_accept_appointments:
        appointment.status = AppointmentStatus.CONFIRMED
    appointment.save(update_fields=["payment_status", "paid_at", "status", "updated_at"])
    return tr
