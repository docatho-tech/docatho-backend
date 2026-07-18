"""Full pharmacy journeys, end to end, driven entirely through the public API.

Unlike the per-segment suites (cart / checkout / fulfilment / invoice), each test
here walks a complete order lifecycle across all three roles — customer, admin and
provider — feeding the output of each step into the next:

    browse/cart -> (prescription) -> checkout -> [payment] -> assign provider
        -> provider fulfilment -> delivered -> invoice

so regressions in the *seams* between steps are caught, not just each step alone.
"""

import hashlib
import hmac
from decimal import Decimal
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from docatho_backend.cart.models import Cart
from docatho_backend.notifications.models import NotificationType
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import Transaction
from docatho_backend.testing.factories import AddressFactory
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import ProviderFactory
from docatho_backend.testing.factories import RxMedicineFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db

SECRET = "rzp_test_secret"


def _sign(order_id: str, payment_id: str, secret: str = SECRET) -> str:
    msg = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


class _FakeRpResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def _add_to_cart(client, medicine, qty=1):
    resp = client.post(
        "/api/cart/add/",
        {"medicine_id": medicine.id, "quantity": qty},
        format="json",
    )
    assert resp.status_code in (200, 201), resp.data


def _fulfil_to_delivered(provider_client, order_id):
    """Provider walks the order through the full fulfilment lifecycle."""
    for st in ("approved", "packed", "out_for_delivery", "delivered"):
        resp = provider_client.patch(
            f"/api/providers/chemist-order-update/{order_id}/",
            {"status": st},
            format="json",
        )
        assert resp.status_code == 200, (st, resp.data)


# --------------------------------------------------------------------------- #
# COD: the simplest complete journey
# --------------------------------------------------------------------------- #
def test_full_cod_journey_cart_to_delivered_to_invoice(auth_client):
    customer = UserFactory()
    AddressFactory(user=customer, is_default=True)
    admin = AdminUserFactory()
    provider = ProviderFactory()
    med = MedicineFactory(price=Decimal("50.00"), mrp=Decimal("60.00"), stock=10)

    cust = auth_client(customer)
    _add_to_cart(cust, med, qty=2)

    # --- checkout (COD) ---
    checkout = cust.post(
        "/api/orders/checkout/",
        {"payment_method": "cod"},
        format="json",
    )
    assert checkout.status_code == 201, checkout.data
    order_id = checkout.data["order"]["id"]
    assert checkout.data["razorpay_order"] is None

    order = Order.objects.get(pk=order_id)
    assert order.stock_reserved is True
    med.refresh_from_db()
    assert med.stock == 8
    assert Cart.objects.get(user=customer).items.count() == 0
    assert customer.notifications.filter(
        notification_type=NotificationType.ORDER_PLACED,
        order=order,
    ).exists()

    # --- admin assigns a fulfilling pharmacy ---
    assign = auth_client(admin).patch(
        f"/api/admin/orders/{order_id}/assign-provider/",
        {"provider_id": provider.id},
        format="json",
    )
    assert assign.status_code == 200, assign.data

    # --- provider fulfils the order end to end ---
    _fulfil_to_delivered(auth_client(provider.user), order_id)
    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED
    assert order.delivered_at is not None
    assert customer.notifications.filter(
        notification_type=NotificationType.ORDER_DELIVERED,
        order=order,
    ).exists()

    # delivering does not return stock
    med.refresh_from_db()
    assert med.stock == 8

    # --- customer downloads the invoice ---
    invoice = cust.get(f"/api/orders/{order_id}/invoice/")
    assert invoice.status_code == 200
    assert invoice["Content-Type"] == "application/pdf"


# --------------------------------------------------------------------------- #
# Online (Razorpay): checkout -> confirm-payment -> fulfilment
# --------------------------------------------------------------------------- #
def test_full_online_journey_with_payment_confirmation(auth_client, settings):
    settings.RAZORPAY_KEY_SECRET = SECRET
    settings.RAZORPAY_KEY_ID = "rzp_test_id"
    customer = UserFactory()
    AddressFactory(user=customer, is_default=True)
    admin = AdminUserFactory()
    provider = ProviderFactory()
    med = MedicineFactory(price=Decimal("100.00"), stock=10)

    cust = auth_client(customer)
    _add_to_cart(cust, med, qty=3)

    rp_order = {"id": "order_E2E", "amount": 30000, "currency": "INR"}
    with mock.patch(
        "docatho_backend.orders.razorpay.requests.post",
        return_value=_FakeRpResponse(rp_order),
    ):
        checkout = cust.post(
            "/api/orders/checkout/",
            {"payment_method": "online"},
            format="json",
        )
    assert checkout.status_code == 201, checkout.data
    order_id = checkout.data["order"]["id"]
    assert checkout.data["razorpay_order"] == rp_order

    order = Order.objects.get(pk=order_id)
    # online defers stock + keeps the cart until payment confirms
    assert order.stock_reserved is False
    med.refresh_from_db()
    assert med.stock == 10
    assert Cart.objects.get(user=customer).items.count() == 1
    # a pending transaction was persisted with the razorpay order id
    assert Transaction.objects.filter(
        transaction_order_id="order_E2E",
        succeeded=False,
    ).exists()

    # --- confirm payment (valid signature) ---
    confirm = cust.post(
        "/api/orders/confirm-payment/",
        {
            "razorpay_order_id": "order_E2E",
            "razorpay_payment_id": "pay_E2E",
            "razorpay_signature": _sign("order_E2E", "pay_E2E"),
        },
        format="json",
    )
    assert confirm.status_code == 200, confirm.data
    order.refresh_from_db()
    med.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.PAID
    assert order.status == Order.Status.CONFIRMED
    assert order.stock_reserved is True
    assert med.stock == 7  # reserved on confirmation
    assert Cart.objects.get(user=customer).items.count() == 0

    # --- assign + fulfil ---
    auth_client(admin).patch(
        f"/api/admin/orders/{order_id}/assign-provider/",
        {"provider_id": provider.id},
        format="json",
    )
    _fulfil_to_delivered(auth_client(provider.user), order_id)
    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED
    assert order.payment_status == Order.PaymentStatus.PAID


# --------------------------------------------------------------------------- #
# Prescription-gated journey with a mixed (Rx + OTC) cart
# --------------------------------------------------------------------------- #
def test_full_rx_journey_upload_then_checkout_then_fulfil(auth_client):
    customer = UserFactory()
    AddressFactory(user=customer, is_default=True)
    provider = ProviderFactory()
    rx_med = RxMedicineFactory(price=Decimal("200.00"), stock=5)
    otc_med = MedicineFactory(price=Decimal("40.00"), stock=5)

    cust = auth_client(customer)
    _add_to_cart(cust, rx_med, qty=1)
    _add_to_cart(cust, otc_med, qty=2)

    # blocked without a prescription
    blocked = cust.post(
        "/api/orders/checkout/",
        {"payment_method": "cod"},
        format="json",
    )
    assert blocked.status_code == 400
    assert "prescription" in str(blocked.data).lower()

    # upload a prescription through the API
    rx_file = SimpleUploadedFile(
        "rx.pdf",
        b"%PDF-1.4 rx",
        content_type="application/pdf",
    )
    upload = cust.post("/api/prescriptions/", {"image": rx_file}, format="multipart")
    assert upload.status_code == 201, upload.data
    prescription_id = upload.data["id"]

    # now checkout succeeds
    checkout = cust.post(
        "/api/orders/checkout/",
        {"payment_method": "cod", "prescription_id": prescription_id},
        format="json",
    )
    assert checkout.status_code == 201, checkout.data
    order = Order.objects.get(pk=checkout.data["order"]["id"])
    assert order.prescription_id == prescription_id
    assert order.items.count() == 2
    assert order.items.filter(prescription_required=True).count() == 1

    rx_med.refresh_from_db()
    otc_med.refresh_from_db()
    assert rx_med.stock == 4
    assert otc_med.stock == 3

    # provider fulfils it
    AdminUserFactory()
    admin_client = auth_client(AdminUserFactory())
    admin_client.patch(
        f"/api/admin/orders/{order.id}/assign-provider/",
        {"provider_id": provider.id},
        format="json",
    )
    _fulfil_to_delivered(auth_client(provider.user), order.id)
    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED


# --------------------------------------------------------------------------- #
# Cancellation & rejection restore inventory
# --------------------------------------------------------------------------- #
def test_customer_cancellation_after_cod_checkout_restores_stock(auth_client):
    customer = UserFactory()
    AddressFactory(user=customer, is_default=True)
    med = MedicineFactory(stock=10)

    cust = auth_client(customer)
    _add_to_cart(cust, med, qty=4)
    checkout = cust.post(
        "/api/orders/checkout/",
        {"payment_method": "cod"},
        format="json",
    )
    order_id = checkout.data["order"]["id"]
    med.refresh_from_db()
    assert med.stock == 6

    cancel = cust.patch(
        f"/api/orders/{order_id}/update-status/",
        {"status": "cancelled"},
        format="json",
    )
    assert cancel.status_code == 200, cancel.data
    order = Order.objects.get(pk=order_id)
    assert order.status == Order.Status.CANCELLED
    assert order.stock_reserved is False
    med.refresh_from_db()
    assert med.stock == 10  # inventory returned


def test_provider_rejection_restores_stock(auth_client):
    customer = UserFactory()
    AddressFactory(user=customer, is_default=True)
    provider = ProviderFactory()
    admin = AdminUserFactory()
    med = MedicineFactory(stock=10)

    cust = auth_client(customer)
    _add_to_cart(cust, med, qty=3)
    order_id = cust.post(
        "/api/orders/checkout/",
        {"payment_method": "cod"},
        format="json",
    ).data["order"]["id"]

    auth_client(admin).patch(
        f"/api/admin/orders/{order_id}/assign-provider/",
        {"provider_id": provider.id},
        format="json",
    )
    reject = auth_client(provider.user).patch(
        f"/api/providers/chemist-order-update/{order_id}/",
        {"status": "rejected"},
        format="json",
    )
    assert reject.status_code == 200, reject.data

    order = Order.objects.get(pk=order_id)
    assert order.status == Order.Status.REJECTED
    med.refresh_from_db()
    assert med.stock == 10


# --------------------------------------------------------------------------- #
# Cross-customer isolation across the whole flow
# --------------------------------------------------------------------------- #
def test_customer_cannot_touch_another_customers_order(auth_client):
    owner = UserFactory()
    AddressFactory(user=owner, is_default=True)
    intruder = UserFactory()
    med = MedicineFactory(stock=10)

    owner_client = auth_client(owner)
    _add_to_cart(owner_client, med, qty=1)
    order_id = owner_client.post(
        "/api/orders/checkout/",
        {"payment_method": "cod"},
        format="json",
    ).data["order"]["id"]

    intruder_client = auth_client(intruder)
    assert intruder_client.get(f"/api/orders/{order_id}/").status_code == 404
    assert intruder_client.get(f"/api/orders/{order_id}/invoice/").status_code == 404
    assert (
        intruder_client.patch(
            f"/api/orders/{order_id}/update-status/",
            {"status": "cancelled"},
            format="json",
        ).status_code
        == 404
    )
