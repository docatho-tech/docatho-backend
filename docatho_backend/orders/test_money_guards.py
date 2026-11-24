"""The money and dispensing preconditions on an order.

Four things that used to be nobody's job: an unpaid online order could be
packed, a Schedule H order could go out on a prescription no pharmacist had
looked at, a COD shortage left a phantom order behind, and cancelling a paid
order returned the stock but never the money.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from docatho_backend.cart.models import Cart
from docatho_backend.cart.models import order_quote
from docatho_backend.medicines.models import DrugSchedule
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import Prescription
from docatho_backend.orders.models import Transaction
from docatho_backend.testing.factories import AddressFactory
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import OrderItemFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_unpaid_online_order_cannot_be_fulfilled():
    order = OrderFactory(
        status=Order.Status.PLACED,
        payment_method=Order.PaymentMethod.ONLINE,
        payment_status=Order.PaymentStatus.FAILED,
    )
    OrderItemFactory(order=order)

    with pytest.raises(ValueError, match="not paid"):
        order.update_status(Order.Status.PACKED)

    order.refresh_from_db()
    assert order.status == Order.Status.PLACED

    order.payment_status = Order.PaymentStatus.PAID
    order.save(update_fields=["payment_status"])
    order.update_status(Order.Status.PACKED)
    order.refresh_from_db()
    assert order.status == Order.Status.PACKED


def test_cod_order_is_fulfillable_while_unpaid():
    """Cash on delivery is unpaid by definition; the guard must not catch it."""
    order = OrderFactory(
        status=Order.Status.PLACED,
        payment_method=Order.PaymentMethod.COD,
        payment_status=Order.PaymentStatus.PENDING,
    )
    OrderItemFactory(order=order)
    order.update_status(Order.Status.PACKED)
    order.refresh_from_db()
    assert order.status == Order.Status.PACKED


def test_scheduled_drug_needs_an_approved_prescription():
    user = UserFactory()
    order = OrderFactory(
        user=user,
        status=Order.Status.PLACED,
        payment_method=Order.PaymentMethod.COD,
    )
    OrderItemFactory(
        order=order,
        medicine=MedicineFactory(schedule=DrugSchedule.H1),
    )

    # No prescription at all.
    with pytest.raises(ValueError, match="prescription is required"):
        order.update_status(Order.Status.APPROVED)

    # Uploaded but unreviewed: still not dispensable.
    rx = Prescription.objects.create(user=user, image="prescriptions/x.jpg")
    order.prescription = rx
    order.save(update_fields=["prescription"])
    with pytest.raises(ValueError, match="not been approved"):
        order.update_status(Order.Status.APPROVED)

    rx.status = Prescription.Status.APPROVED
    rx.save(update_fields=["status"])
    order.update_status(Order.Status.APPROVED)
    order.refresh_from_db()
    assert order.status == Order.Status.APPROVED


def test_cod_shortage_leaves_no_order_behind(auth_client):
    """`return` inside an atomic block commits it; this must roll back."""
    user = UserFactory()
    address = AddressFactory(user=user)
    medicine = MedicineFactory(stock=10)
    cart = Cart.objects.create(user=user)
    cart.add_item(medicine, quantity=1)

    # The shortage branch is only reachable when stock runs out between the
    # view's soft check and `reserve_stock` — a real race, forced here.
    with patch.object(
        Order,
        "reserve_stock",
        side_effect=ValueError("Out of stock: Medicine 1"),
    ):
        resp = auth_client(user).post(
            "/api/orders/checkout/",
            {"payment_method": "cod", "address_id": address.pk},
            format="json",
        )

    assert resp.status_code == 400, resp.data
    assert not Order.objects.filter(user=user).exists()


def test_checkout_refuses_a_stale_cart_price(auth_client):
    user = UserFactory()
    medicine = MedicineFactory(price=Decimal("100.00"), stock=10)
    cart = Cart.objects.create(user=user)
    cart.add_item(medicine, quantity=2)

    medicine.price = Decimal("140.00")
    medicine.save(update_fields=["price"])

    resp = auth_client(user).post(
        "/api/orders/checkout/",
        {"payment_method": "cod"},
        format="json",
    )

    assert resp.status_code == 409, resp.data
    assert resp.data["price_changed"] is True
    assert not Order.objects.filter(user=user).exists()
    cart.refresh_from_db()
    assert cart.subtotal == Decimal("280.00")


def test_cart_quote_is_the_amount_checkout_charges():
    """One function behind both figures, so the cart cannot quote a fiction."""
    quote = order_quote(Decimal("500.00"))
    assert (
        quote["payable_total"]
        == Decimal("500.00") + quote["delivery_fee"] - quote["order_discount"]
    )


def test_cancelling_a_paid_online_order_refunds_it(django_capture_on_commit_callbacks):
    order = OrderFactory(
        status=Order.Status.CONFIRMED,
        payment_method=Order.PaymentMethod.ONLINE,
        payment_status=Order.PaymentStatus.PAID,
        total=Decimal("250.00"),
    )
    OrderItemFactory(order=order)
    Transaction.objects.create(
        order=order,
        transaction_order_id="order_test",
        razorpay_payment_id="pay_test",
        amount=Decimal("250.00"),
        succeeded=True,
    )

    # The refund is queued on commit so the gateway call happens outside the
    # transaction holding the order row.
    with patch(
        "docatho_backend.orders.razorpay.RazorpayClient.refund",
        return_value={"id": "rfnd_test", "status": "processed"},
    ) as refund:
        with django_capture_on_commit_callbacks(execute=True):
            order.update_status(Order.Status.CANCELLED)

    refund.assert_called_once()
    assert refund.call_args.args[1] == Decimal("250.00")
    order.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.REFUNDED

    # Idempotent: a second cancellation must not refund twice.
    with patch(
        "docatho_backend.orders.razorpay.RazorpayClient.refund",
    ) as refund_again:
        with django_capture_on_commit_callbacks(execute=True):
            order.update_status(Order.Status.CANCELLED)
    refund_again.assert_not_called()


def test_cod_cancellation_takes_no_refund(django_capture_on_commit_callbacks):
    order = OrderFactory(
        status=Order.Status.PLACED,
        payment_method=Order.PaymentMethod.COD,
        payment_status=Order.PaymentStatus.PENDING,
    )
    with patch("docatho_backend.orders.razorpay.RazorpayClient.refund") as refund:
        with django_capture_on_commit_callbacks(execute=True):
            order.update_status(Order.Status.CANCELLED)
    refund.assert_not_called()
