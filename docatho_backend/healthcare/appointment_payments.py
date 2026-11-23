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


def is_free_appointment(appointment: Appointment) -> bool:
    """A consultation with nothing to charge.

    Razorpay rejects any order below ₹1, so a doctor whose online fee is 0 —
    most of the directory — cannot be booked through the gateway at all. The
    fee is read from the doctor at creation (never sent by the client), so
    trusting it here does not reopen the free-consultation hole.
    """
    return appointment.fee <= 0


def settle_free_appointment(appointment: Appointment) -> None:
    """Mark a zero-fee consultation paid without touching the gateway."""
    appointment.payment_status = AppointmentPaymentStatus.PAID
    appointment.paid_at = timezone.now()
    appointment.payment_method = "free"
    update_fields = ["payment_status", "paid_at", "payment_method", "updated_at"]
    if appointment.doctor.auto_accept_appointments:
        appointment.status = AppointmentStatus.CONFIRMED
        update_fields.append("status")
    appointment.save(update_fields=update_fields)


@transaction.atomic
def create_appointment_checkout(appointment: Appointment) -> dict[str, Any]:
    if appointment.consultation_mode != "online":
        raise ValueError("Only online consultations require prepayment")
    if is_free_appointment(appointment):
        raise ValueError("Nothing to charge for this appointment")

    # Lock the appointment for the whole of checkout. Two taps on "Pay" used to
    # race past the reuse check below and each open its own Razorpay order, with
    # no refund machinery anywhere in this codebase to undo the second charge.
    # ponytail: the row lock is held across the Razorpay HTTP call (15s
    # timeout). Fine at this volume; move to a short-lived cache lock if
    # checkout latency starts holding database connections.
    locked = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if locked.payment_status == AppointmentPaymentStatus.PAID:
        raise ValueError("Appointment already paid")

    # Idempotent: the app's "retry payment" button calls checkout again after a
    # dropped confirm, so hand back the attempt that is already open instead of
    # minting a second one. Reuse is safe because Razorpay refuses a second
    # payment on an order it has already captured, and a capture that the app
    # never confirmed is settled by the webhook (see `handle_webhook`), which
    # trips the "already paid" guard above before we get here.
    open_attempt = (
        appointment.payment_transactions.filter(succeeded=False)
        .exclude(transaction_order_id="")
        .order_by("-created_at")
        .first()
    )

    amount_paisa = int((appointment.fee * 100).to_integral_value())
    if open_attempt is not None:
        return open_attempt.raw_response or {
            "id": open_attempt.transaction_order_id,
            "amount": amount_paisa,
            "currency": "INR",
        }

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
def settle_appointment_payment(
    tr: AppointmentPaymentTransaction,
    razorpay_payment_id: str = "",
    razorpay_signature: str | None = None,
    raw_response: dict[str, Any] | None = None,
) -> AppointmentPaymentTransaction:
    """Mark an appointment's payment settled. The only place that does.

    Both ``confirm-payment`` and the Razorpay webhook land here, and either can
    arrive first or twice over — the app confirms on a 10s timeout while
    Razorpay retries deliveries — so this is idempotent: once the appointment
    reads paid, a repeat call is a no-op rather than a second set of side
    effects.
    """
    appointment = Appointment.objects.select_for_update().get(pk=tr.appointment_id)
    if appointment.payment_status == AppointmentPaymentStatus.PAID:
        return tr

    tr.razorpay_payment_id = razorpay_payment_id or tr.razorpay_payment_id
    if razorpay_signature is not None:
        tr.razorpay_signature = razorpay_signature
    tr.succeeded = True
    tr.paid_at = timezone.now()
    if raw_response is not None:
        tr.raw_response = raw_response
    tr.save()

    appointment.payment_status = AppointmentPaymentStatus.PAID
    appointment.paid_at = timezone.now()
    update_fields = ["payment_status", "paid_at", "updated_at"]
    if appointment.doctor.auto_accept_appointments:
        appointment.status = AppointmentStatus.CONFIRMED
        update_fields.append("status")
    appointment.save(update_fields=update_fields)
    return tr


def confirm_appointment_payment(
    appointment: Appointment,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str | None = None,
    raw_response: dict[str, Any] | None = None,
) -> AppointmentPaymentTransaction:
    client = RazorpayClient()
    # Unconditional: a missing signature used to skip verification entirely, so
    # a patient could mark their own consultation paid by posting the order id
    # from /checkout/ with no signature at all.
    ok = client.verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature or "")
    if not ok:
        raise ValueError("Invalid signature")

    tr = AppointmentPaymentTransaction.objects.filter(
        appointment=appointment,
        transaction_order_id=razorpay_order_id,
    ).first()
    if tr is None:
        raise ValueError("No payment transaction found for this appointment")

    return settle_appointment_payment(
        tr,
        razorpay_payment_id,
        razorpay_signature or "",
        raw_response,
    )
