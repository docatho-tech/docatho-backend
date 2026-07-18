"""Razorpay webhook: ``/api/webhooks/razorpay/`` (public, EP-11).

Covers signature verification, payment.captured / payment.failed side effects on
the Order & Transaction, unconfigured secret, and non-payment events.
"""

import hashlib
import hmac
import json
from decimal import Decimal

import pytest

from docatho_backend.orders.models import Order
from docatho_backend.orders.models import Transaction
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db

WEBHOOK_SECRET = "whsec_test"


def _webhook_sig(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payment_payload(event, *, rp_order_id, rp_payment_id="pay_WH", amount=10000):
    return {
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "id": rp_payment_id,
                    "order_id": rp_order_id,
                    "amount": amount,
                },
            },
        },
    }


def _post_webhook(api_client, payload, *, secret=WEBHOOK_SECRET, sign=True):
    body = json.dumps(payload).encode()
    headers = {}
    if sign:
        headers["HTTP_X_RAZORPAY_SIGNATURE"] = _webhook_sig(body, secret)
    return api_client.post(
        "/api/webhooks/razorpay/",
        data=body,
        content_type="application/json",
        **headers,
    )


@pytest.fixture(autouse=True)
def _webhook_secret(settings):
    settings.RAZORPAY_WEBHOOK_SECRET = WEBHOOK_SECRET


def _order_with_txn(rp_order_id="order_WH"):
    order = OrderFactory(
        user=UserFactory(),
        payment_method=Order.PaymentMethod.ONLINE,
        payment_status=Order.PaymentStatus.PENDING,
        total=Decimal("100.00"),
    )
    tr = Transaction.objects.create(
        order=order,
        provider="razorpay",
        transaction_order_id=rp_order_id,
        amount=Decimal("100.00"),
        succeeded=False,
    )
    return order, tr


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #
def test_webhook_payment_captured_marks_order_paid(api_client):
    order, tr = _order_with_txn("order_CAP")
    resp = _post_webhook(
        api_client,
        _payment_payload("payment.captured", rp_order_id="order_CAP"),
    )
    assert resp.status_code == 200, resp.data
    assert resp.data["event"] == "payment.captured"

    order.refresh_from_db()
    tr.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.PAID
    assert order.status == Order.Status.CONFIRMED
    assert tr.succeeded is True
    assert tr.paid_at is not None
    assert tr.razorpay_payment_id == "pay_WH"


def test_webhook_payment_failed_marks_order_failed(api_client):
    order, tr = _order_with_txn("order_FAIL")
    resp = _post_webhook(
        api_client,
        _payment_payload("payment.failed", rp_order_id="order_FAIL"),
    )
    assert resp.status_code == 200, resp.data

    order.refresh_from_db()
    tr.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.FAILED
    assert tr.succeeded is False


def test_webhook_authorized_event_does_not_mark_paid(api_client):
    """payment.authorized is acknowledged but must not settle the order."""
    order, tr = _order_with_txn("order_AUTH")
    resp = _post_webhook(
        api_client,
        _payment_payload("payment.authorized", rp_order_id="order_AUTH"),
    )
    assert resp.status_code == 200
    order.refresh_from_db()
    tr.refresh_from_db()
    assert tr.succeeded is False
    assert order.payment_status != Order.PaymentStatus.PAID


def test_webhook_ignores_non_payment_event(api_client):
    order, tr = _order_with_txn("order_NOOP")
    resp = _post_webhook(
        api_client,
        {"event": "refund.processed", "payload": {}},
    )
    assert resp.status_code == 200
    assert resp.data["event"] == "refund.processed"
    order.refresh_from_db()
    tr.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.PENDING
    assert tr.succeeded is False


# --------------------------------------------------------------------------- #
# Security / configuration edge cases
# --------------------------------------------------------------------------- #
def test_webhook_rejects_invalid_signature(api_client):
    order, tr = _order_with_txn("order_BADSIG")
    body = json.dumps(_payment_payload("payment.captured", rp_order_id="order_BADSIG"))
    resp = api_client.post(
        "/api/webhooks/razorpay/",
        data=body.encode(),
        content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE="not-the-real-signature",
    )
    assert resp.status_code == 400
    order.refresh_from_db()
    tr.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.PENDING
    assert tr.succeeded is False


def test_webhook_signature_from_wrong_secret_is_rejected(api_client):
    _order_with_txn("order_WRONGSEC")
    resp = _post_webhook(
        api_client,
        _payment_payload("payment.captured", rp_order_id="order_WRONGSEC"),
        secret="a_different_secret",
    )
    assert resp.status_code == 400


def test_webhook_without_configured_secret_returns_400(api_client, settings):
    settings.RAZORPAY_WEBHOOK_SECRET = ""
    resp = _post_webhook(
        api_client,
        _payment_payload("payment.captured", rp_order_id="order_X"),
    )
    assert resp.status_code == 400


def test_webhook_is_public_no_auth_required(api_client):
    """An unauthenticated request must reach the handler (fails on signature, not auth)."""
    _order_with_txn("order_PUB")
    resp = _post_webhook(
        api_client,
        _payment_payload("payment.captured", rp_order_id="order_PUB"),
    )
    assert resp.status_code == 200
