"""Online payment confirmation: ``/api/orders/confirm-payment/``.

Exercises the real signature-verification path (no mocking of the crypto), the
post-payment stock reservation, cart clearing, notifications and every edge case
around a bad/missing/duplicate confirmation.
"""

import hashlib
import hmac
from decimal import Decimal

import pytest

from docatho_backend.cart.models import Cart
from docatho_backend.notifications.models import NotificationType
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import Transaction
from docatho_backend.orders.razorpay import RazorpayClient
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import OrderItemFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db

SECRET = "rzp_test_secret"


def _sign(order_id: str, payment_id: str, secret: str = SECRET) -> str:
    """Reproduce Razorpay's client-side signature: HMAC(order|payment)."""
    msg = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _online_order_awaiting_payment(user, *, stock=10, qty=2, rp_order_id="order_RP1"):
    """An online order created but not yet paid, plus its pending Transaction.

    Mirrors the state left by ``checkout`` with ``payment_method=online``:
    stock is NOT yet reserved and a Transaction row carries the razorpay order id.
    """
    med = MedicineFactory(price=Decimal("100.00"), stock=stock)
    order = OrderFactory(
        user=user,
        payment_method=Order.PaymentMethod.ONLINE,
        payment_status=Order.PaymentStatus.PENDING,
    )
    OrderItemFactory(
        order=order,
        medicine=med,
        quantity=qty,
        unit_price=Decimal("100.00"),
    )
    order.recalc_totals()
    tr = Transaction.objects.create(
        order=order,
        provider="razorpay",
        transaction_order_id=rp_order_id,
        amount=order.total,
        succeeded=False,
    )
    return order, med, tr


@pytest.fixture(autouse=True)
def _razorpay_secret(settings):
    settings.RAZORPAY_KEY_SECRET = SECRET
    settings.RAZORPAY_KEY_ID = "rzp_test_id"


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_confirm_payment_marks_paid_reserves_stock_and_clears_cart(auth_client):
    user = UserFactory()
    order, med, _ = _online_order_awaiting_payment(user, stock=10, qty=2)
    # a lingering cart (online checkout does not clear it until confirmation)
    cart, _ = Cart.objects.get_or_create(user=user)
    cart.add_item(med, quantity=2)
    payment_id = "pay_RP1"

    resp = auth_client(user).post(
        "/api/orders/confirm-payment/",
        {
            "razorpay_order_id": "order_RP1",
            "razorpay_payment_id": payment_id,
            "razorpay_signature": _sign("order_RP1", payment_id),
        },
        format="json",
    )

    assert resp.status_code == 200, resp.data
    assert resp.data["transaction"]["succeeded"] is True

    order.refresh_from_db()
    med.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.PAID
    assert order.status == Order.Status.CONFIRMED
    assert order.stock_reserved is True
    assert med.stock == 8  # 10 - 2 reserved on confirmation
    assert Cart.objects.get(user=user).items.count() == 0
    assert user.notifications.filter(
        notification_type=NotificationType.PAYMENT,
        order=order,
    ).exists()


def test_confirm_payment_persists_transaction_details(auth_client):
    user = UserFactory()
    order, _, tr = _online_order_awaiting_payment(user)
    payment_id = "pay_DETAILS"

    auth_client(user).post(
        "/api/orders/confirm-payment/",
        {
            "razorpay_order_id": "order_RP1",
            "razorpay_payment_id": payment_id,
            "razorpay_signature": _sign("order_RP1", payment_id),
        },
        format="json",
    )

    tr.refresh_from_db()
    assert tr.succeeded is True
    assert tr.razorpay_payment_id == payment_id
    assert tr.paid_at is not None


# --------------------------------------------------------------------------- #
# Bad signature / unknown order / validation
# --------------------------------------------------------------------------- #
def test_confirm_payment_rejects_invalid_signature(auth_client):
    user = UserFactory()
    order, med, tr = _online_order_awaiting_payment(user, stock=10, qty=2)

    resp = auth_client(user).post(
        "/api/orders/confirm-payment/",
        {
            "razorpay_order_id": "order_RP1",
            "razorpay_payment_id": "pay_RP1",
            "razorpay_signature": "deadbeef_not_a_valid_signature",
        },
        format="json",
    )

    assert resp.status_code == 400
    tr.refresh_from_db()
    order.refresh_from_db()
    med.refresh_from_db()
    assert tr.succeeded is False
    assert order.payment_status == Order.PaymentStatus.PENDING
    assert order.stock_reserved is False
    assert med.stock == 10  # nothing reserved
    # NOTE: razorpay.confirm_payment tries to record the failed attempt on the
    # transaction, but that write is rolled back by the method's surrounding
    # @transaction.atomic when it raises — so nothing about the txn settles.
    assert not user.notifications.filter(
        notification_type=NotificationType.PAYMENT,
    ).exists()


def test_confirm_payment_unknown_order_id_returns_400(auth_client):
    user = UserFactory()
    resp = auth_client(user).post(
        "/api/orders/confirm-payment/",
        {
            "razorpay_order_id": "order_DOES_NOT_EXIST",
            "razorpay_payment_id": "pay_X",
            "razorpay_signature": _sign("order_DOES_NOT_EXIST", "pay_X"),
        },
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"razorpay_order_id": "order_RP1"},
        {"razorpay_order_id": "order_RP1", "razorpay_payment_id": "pay_RP1"},
    ],
)
def test_confirm_payment_missing_fields_returns_400(auth_client, payload):
    user = UserFactory()
    _online_order_awaiting_payment(user)
    resp = auth_client(user).post(
        "/api/orders/confirm-payment/",
        payload,
        format="json",
    )
    assert resp.status_code == 400


def test_confirm_payment_requires_authentication(api_client):
    resp = api_client.post(
        "/api/orders/confirm-payment/",
        {
            "razorpay_order_id": "order_RP1",
            "razorpay_payment_id": "pay_RP1",
            "razorpay_signature": "sig",
        },
        format="json",
    )
    assert resp.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# Idempotency & stock edge cases
# --------------------------------------------------------------------------- #
def test_confirm_payment_is_idempotent_on_double_confirm(auth_client):
    """A retried confirmation must not double-deduct stock."""
    user = UserFactory()
    order, med, _ = _online_order_awaiting_payment(user, stock=10, qty=2)
    body = {
        "razorpay_order_id": "order_RP1",
        "razorpay_payment_id": "pay_RP1",
        "razorpay_signature": _sign("order_RP1", "pay_RP1"),
    }
    client = auth_client(user)

    first = client.post("/api/orders/confirm-payment/", body, format="json")
    second = client.post("/api/orders/confirm-payment/", body, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    med.refresh_from_db()
    assert med.stock == 8  # deducted once, not twice


def test_confirm_payment_with_stock_shortage_flags_for_review_but_succeeds(auth_client):
    """If stock vanished after checkout, payment still succeeds and the order is
    routed to PROCESSING for manual review rather than failing the confirmation."""
    user = UserFactory()
    order, med, _ = _online_order_awaiting_payment(user, stock=10, qty=5)
    # inventory dropped below the ordered quantity after checkout
    med.stock = 1
    med.save(update_fields=["stock"])

    resp = auth_client(user).post(
        "/api/orders/confirm-payment/",
        {
            "razorpay_order_id": "order_RP1",
            "razorpay_payment_id": "pay_RP1",
            "razorpay_signature": _sign("order_RP1", "pay_RP1"),
        },
        format="json",
    )

    assert resp.status_code == 200, resp.data
    order.refresh_from_db()
    med.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.PAID
    assert order.status == Order.Status.PROCESSING
    assert order.stock_reserved is False
    assert med.stock == 1  # untouched — flagged for ops, not deducted
    assert "manual review" in (order.notes or "").lower()


# --------------------------------------------------------------------------- #
# RazorpayClient unit-level signature checks
# --------------------------------------------------------------------------- #
def test_verify_payment_signature_accepts_correct_and_rejects_wrong():
    client = RazorpayClient(key_id="id", key_secret=SECRET)
    good = _sign("order_1", "pay_1")
    assert client.verify_payment_signature("order_1", "pay_1", good) is True
    assert client.verify_payment_signature("order_1", "pay_1", "wrong") is False


@pytest.mark.parametrize(
    ("oid", "pid", "sig"),
    [("", "pay", "sig"), ("order", "", "sig"), ("order", "pay", "")],
)
def test_verify_payment_signature_rejects_missing_args(oid, pid, sig):
    client = RazorpayClient(key_id="id", key_secret=SECRET)
    assert client.verify_payment_signature(oid, pid, sig) is False


def test_client_confirm_payment_raises_for_unknown_transaction():
    client = RazorpayClient(key_id="id", key_secret=SECRET)
    with pytest.raises(ValueError):
        client.confirm_payment(
            razorpay_order_id="order_missing",
            razorpay_payment_id="pay",
            razorpay_signature=_sign("order_missing", "pay"),
        )
