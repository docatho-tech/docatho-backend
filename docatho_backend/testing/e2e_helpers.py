"""Shared helpers for cross-role API E2E tests."""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal
from unittest import mock

RAZORPAY_TEST_SECRET = "rzp_test_secret_e2e"


class FakeRpResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def razorpay_signature(order_id: str, payment_id: str, secret: str = RAZORPAY_TEST_SECRET) -> str:
    msg = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def mock_razorpay_order(order_id: str, amount_rupees: Decimal | float) -> dict:
    return {
        "id": order_id,
        "amount": int(Decimal(str(amount_rupees)) * 100),
        "currency": "INR",
    }


def patch_appointment_razorpay(order_payload: dict):
    return mock.patch(
        "docatho_backend.healthcare.appointment_payments.requests.post",
        return_value=FakeRpResponse(order_payload),
    )


def patch_order_razorpay(order_payload: dict):
    return mock.patch(
        "docatho_backend.orders.razorpay.requests.post",
        return_value=FakeRpResponse(order_payload),
    )


def add_to_cart(client, medicine, qty=1):
    resp = client.post(
        "/api/cart/add/",
        {"medicine_id": medicine.id, "quantity": qty},
        format="json",
    )
    assert resp.status_code in (200, 201), resp.data
    return resp
