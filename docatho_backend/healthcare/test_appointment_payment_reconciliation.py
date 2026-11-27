"""Appointment money paths that cannot be allowed to drift.

Two live incidents are covered here: an appointment payment captured at Razorpay
that the app never confirmed (confirm-payment runs on a 10s timeout) used to
leave the appointment pending forever with the money taken, and the "retry
payment" button used to mint a second Razorpay order — a double charge, with no
refund machinery anywhere in this codebase to undo it.
"""

import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentPaymentTransaction
from docatho_backend.testing.e2e_helpers import mock_razorpay_order
from docatho_backend.testing.e2e_helpers import patch_appointment_razorpay
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db

WEBHOOK_SECRET = "whsec_test"
RP_ORDER_ID = "order_appt_wh"
RP_PAYMENT_ID = "pay_appt_wh"


@pytest.fixture(autouse=True)
def _webhook_secret(settings):
    settings.RAZORPAY_WEBHOOK_SECRET = WEBHOOK_SECRET


def _post_webhook(api_client, payload):
    body = json.dumps(payload).encode()
    return api_client.post(
        "/api/webhooks/razorpay/",
        data=body,
        content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE=hmac.new(
            WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest(),
    )


def _payment_payload(event, *, rp_order_id=RP_ORDER_ID, notes=None):
    entity = {"id": RP_PAYMENT_ID, "order_id": rp_order_id, "amount": 50000}
    if notes is not None:
        entity["notes"] = notes
    return {"event": event, "payload": {"payment": {"entity": entity}}}


def _pending_online_appointment(fee=Decimal("500.00")):
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    return Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=timezone.now() + timedelta(days=1),
        consultation_mode="online",
        fee=fee,
        payment_method="online",
        payment_status=AppointmentPaymentStatus.PENDING,
    )


def _open_attempt(appointment, rp_order_id=RP_ORDER_ID):
    return AppointmentPaymentTransaction.objects.create(
        appointment=appointment,
        provider="razorpay",
        transaction_order_id=rp_order_id,
        amount=appointment.fee,
        succeeded=False,
    )


def test_webhook_capture_settles_a_pending_appointment(api_client):
    """The regression: a captured appointment payment nothing reconciled."""
    appointment = _pending_online_appointment()
    tr = _open_attempt(appointment)

    resp = _post_webhook(api_client, _payment_payload("payment.captured"))
    assert resp.status_code == 200, resp.data

    appointment.refresh_from_db()
    tr.refresh_from_db()
    assert appointment.payment_status == AppointmentPaymentStatus.PAID
    assert appointment.paid_at is not None
    assert tr.succeeded is True
    assert tr.razorpay_payment_id == RP_PAYMENT_ID


def test_webhook_capture_without_order_id_match_falls_back_to_notes(api_client):
    """Razorpay echoes the checkout notes, which carry the appointment id."""
    appointment = _pending_online_appointment()
    _open_attempt(appointment, rp_order_id="order_something_else")

    resp = _post_webhook(
        api_client,
        _payment_payload(
            "payment.captured",
            rp_order_id="order_unknown",
            notes={"appointment_id": str(appointment.pk)},
        ),
    )
    assert resp.status_code == 200, resp.data
    appointment.refresh_from_db()
    assert appointment.payment_status == AppointmentPaymentStatus.PAID


def test_webhook_delivered_twice_does_not_double_apply(api_client):
    """Razorpay retries deliveries; the second must be a no-op."""
    appointment = _pending_online_appointment()
    tr = _open_attempt(appointment)

    captured = _payment_payload("payment.captured")
    assert _post_webhook(api_client, captured).status_code == 200
    appointment.refresh_from_db()
    tr.refresh_from_db()
    first_paid_at = appointment.paid_at
    first_tr_paid_at = tr.paid_at

    captured = _payment_payload("payment.captured")
    assert _post_webhook(api_client, captured).status_code == 200
    appointment.refresh_from_db()
    tr.refresh_from_db()
    assert appointment.paid_at == first_paid_at
    assert tr.paid_at == first_tr_paid_at
    assert appointment.payment_transactions.count() == 1


def test_late_payment_failed_does_not_unpay_a_settled_appointment(api_client):
    """An abandoned earlier attempt failing must not undo a capture."""
    appointment = _pending_online_appointment()
    tr = _open_attempt(appointment)
    captured = _payment_payload("payment.captured")
    assert _post_webhook(api_client, captured).status_code == 200

    resp = _post_webhook(api_client, _payment_payload("payment.failed"))
    assert resp.status_code == 200, resp.data

    appointment.refresh_from_db()
    tr.refresh_from_db()
    assert appointment.payment_status == AppointmentPaymentStatus.PAID
    assert tr.succeeded is True


def test_checkout_twice_reuses_the_same_razorpay_order(auth_client, settings):
    """The retry button must not be able to open a second charge."""
    settings.RAZORPAY_KEY_ID = "rzp_test_id"
    settings.RAZORPAY_KEY_SECRET = "rzp_test_secret"
    appointment = _pending_online_appointment()
    client = auth_client(appointment.patient)
    url = f"/api/healthcare/appointments/{appointment.pk}/checkout/"

    rp_order = mock_razorpay_order(RP_ORDER_ID, appointment.fee)
    with patch_appointment_razorpay(rp_order) as post:
        first = client.post(url)
        second = client.post(url)

    assert first.status_code == 200, first.data
    assert second.status_code == 200, second.data
    assert first.data["razorpay_order"]["id"] == RP_ORDER_ID
    assert second.data["razorpay_order"]["id"] == RP_ORDER_ID
    assert (
        second.data["razorpay_order"]["amount"]
        == first.data["razorpay_order"]["amount"]
    )
    # One gateway order, one ledger row: no second charge is reachable.
    assert post.call_count == 1
    assert appointment.payment_transactions.count() == 1
