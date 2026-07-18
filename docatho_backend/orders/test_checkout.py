"""Checkout path: COD, online (mocked gateway), Rx-gate, commission, stock."""

from decimal import Decimal
from unittest import mock

import pytest

from docatho_backend.cart.models import Cart
from docatho_backend.orders.models import Order
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import PrescriptionFactory
from docatho_backend.testing.factories import RxMedicineFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _cart_with(user, medicine, qty=1):
    cart, _ = Cart.objects.get_or_create(user=user)
    cart.add_item(medicine, quantity=qty)
    return cart


def test_cod_checkout_creates_order_reserves_stock_and_clears_cart(auth_client):
    user = UserFactory()
    med = MedicineFactory(price=Decimal("50.00"), mrp=Decimal("60.00"), stock=10)
    _cart_with(user, med, qty=2)
    client = auth_client(user)

    resp = client.post("/api/orders/checkout/", {"payment_method": "cod"}, format="json")

    assert resp.status_code == 201, resp.data
    body = resp.data
    assert body["razorpay_order"] is None
    order = Order.objects.get(order_number=body["order"]["order_number"])
    assert order.payment_method == "cod"
    assert order.payment_status == "pending"
    assert order.stock_reserved is True
    med.refresh_from_db()
    assert med.stock == 8  # 10 - 2 reserved
    # cart emptied
    assert Cart.objects.get(user=user).items.count() == 0


def test_cod_checkout_applies_discount_and_commission(auth_client, settings):
    settings.PHARMACY_ORDER_DISCOUNT_PERCENT = 10.0
    settings.PHARMACY_COMMISSION_PERCENT = 20.0
    user = UserFactory()
    med = MedicineFactory(price=Decimal("100.00"), stock=5)
    _cart_with(user, med, qty=1)
    client = auth_client(user)

    resp = client.post("/api/orders/checkout/", {"payment_method": "cod"}, format="json")
    order = Order.objects.get(pk=resp.data["order"]["id"])

    assert order.subtotal == Decimal("100.00")
    assert order.discount_amount == Decimal("10.00")
    assert order.total == Decimal("90.00")
    # commission is 20% of subtotal, provider gets the rest
    assert order.commission_amount == Decimal("20.00")
    assert order.provider_earning == Decimal("80.00")


def test_rx_gate_blocks_checkout_without_prescription(auth_client):
    user = UserFactory()
    rx_med = RxMedicineFactory(stock=5)
    _cart_with(user, rx_med, qty=1)
    client = auth_client(user)

    resp = client.post("/api/orders/checkout/", {"payment_method": "cod"}, format="json")

    assert resp.status_code == 400
    assert "prescription" in str(resp.data).lower()
    assert Order.objects.count() == 0


def test_rx_gate_allows_checkout_with_valid_prescription(auth_client):
    user = UserFactory()
    rx_med = RxMedicineFactory(stock=5)
    _cart_with(user, rx_med, qty=1)
    prescription = PrescriptionFactory(user=user)
    client = auth_client(user)

    resp = client.post(
        "/api/orders/checkout/",
        {"payment_method": "cod", "prescription_id": prescription.id},
        format="json",
    )

    assert resp.status_code == 201, resp.data
    order = Order.objects.get(pk=resp.data["order"]["id"])
    assert order.prescription_id == prescription.id
    assert order.items.filter(prescription_required=True).exists()


def test_rx_gate_rejects_other_users_prescription(auth_client):
    user = UserFactory()
    other = UserFactory()
    rx_med = RxMedicineFactory(stock=5)
    _cart_with(user, rx_med, qty=1)
    someone_elses = PrescriptionFactory(user=other)
    client = auth_client(user)

    resp = client.post(
        "/api/orders/checkout/",
        {"payment_method": "cod", "prescription_id": someone_elses.id},
        format="json",
    )
    assert resp.status_code == 400


def test_checkout_blocks_when_out_of_stock(auth_client):
    user = UserFactory()
    med = MedicineFactory(stock=1)
    _cart_with(user, med, qty=3)
    client = auth_client(user)

    resp = client.post("/api/orders/checkout/", {"payment_method": "cod"}, format="json")
    assert resp.status_code == 400
    assert "stock" in str(resp.data).lower()


def test_empty_cart_checkout_rejected(auth_client):
    user = UserFactory()
    Cart.objects.create(user=user)
    client = auth_client(user)
    resp = client.post("/api/orders/checkout/", {"payment_method": "cod"}, format="json")
    assert resp.status_code == 400


def test_online_checkout_calls_gateway_and_defers_stock(auth_client):
    user = UserFactory()
    med = MedicineFactory(stock=10)
    _cart_with(user, med, qty=2)
    client = auth_client(user)

    fake_rp = {"id": "order_TEST123", "amount": 10000, "currency": "INR"}
    with mock.patch(
        "docatho_backend.orders.views.RazorpayClient.create_order",
        return_value=fake_rp,
    ) as create_order:
        resp = client.post(
            "/api/orders/checkout/", {"payment_method": "online"}, format="json",
        )

    assert resp.status_code == 201, resp.data
    assert resp.data["razorpay_order"] == fake_rp
    create_order.assert_called_once()
    order = Order.objects.get(pk=resp.data["order"]["id"])
    # Online orders reserve stock only after payment confirmation.
    assert order.stock_reserved is False
    med.refresh_from_db()
    assert med.stock == 10


def test_checkout_requires_authentication(api_client):
    resp = api_client.post("/api/orders/checkout/", {"payment_method": "cod"}, format="json")
    assert resp.status_code in (401, 403)
