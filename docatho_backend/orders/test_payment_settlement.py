"""The webhook must finish a paid order, and nobody may confirm another's.

Both holes were invisible unless you follow one patient whose `confirm-payment`
never reached us: stock stayed unreserved, the cart stayed full, and the
gateway triple was enough to read a stranger's order.
"""

import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from django.test import override_settings

from docatho_backend.cart.models import Cart
from docatho_backend.cart.models import CartItem
from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import Transaction
from docatho_backend.orders.razorpay import RazorpayClient
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import OrderItemFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db

WEBHOOK_SECRET = "test-webhook-secret"  # noqa: S105 - test fixture, not a credential


def _captured_payload(rp_order_id: str, amount_paisa: int = 10000) -> dict:
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test123",
                    "order_id": rp_order_id,
                    "amount": amount_paisa,
                },
            },
        },
    }


def _deliver(payload: dict) -> dict:
    body = json.dumps(payload).encode()
    signature = hmac.new(
        WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    with override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET):
        return RazorpayClient().handle_webhook(body, signature)


def _paid_order_awaiting_settlement():
    user = UserFactory()
    medicine = MedicineFactory(stock=10)
    order = OrderFactory(
        user=user,
        status=Order.Status.PLACED,
        payment_method=Order.PaymentMethod.ONLINE,
    )
    OrderItemFactory(order=order, medicine=medicine, quantity=3)
    order.recalc_totals()

    cart = Cart.objects.create(user=user)
    CartItem.objects.create(
        cart=cart,
        medicine=medicine,
        quantity=3,
        unit_price=Decimal("100.00"),
    )

    tr = Transaction.objects.create(
        order=order,
        amount=order.total,
        transaction_order_id="order_rp_test_1",
    )
    return user, medicine, order, cart, tr


def test_webhook_capture_reserves_stock_and_empties_the_cart():
    """Without this the patient checks out again and pays twice."""
    _user, medicine, order, cart, tr = _paid_order_awaiting_settlement()

    _deliver(_captured_payload(tr.transaction_order_id))

    order.refresh_from_db()
    medicine.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.PAID
    assert order.stock_reserved is True
    assert medicine.stock == 7
    assert cart.items.count() == 0


def test_a_second_capture_does_not_double_deduct_or_double_notify():
    """confirm-payment and the webhook can both land, in either order."""
    user, medicine, order, _cart, tr = _paid_order_awaiting_settlement()

    _deliver(_captured_payload(tr.transaction_order_id))
    _deliver(_captured_payload(tr.transaction_order_id))

    medicine.refresh_from_db()
    assert medicine.stock == 7
    assert (
        Notification.objects.filter(
            recipient=user,
            notification_type=NotificationType.PAYMENT,
            order=order,
        ).count()
        == 1
    )


def test_confirm_payment_cannot_touch_another_patients_order():
    """The gateway triple is not a secret between patients."""
    _user, _medicine, _order, _cart, tr = _paid_order_awaiting_settlement()
    stranger = UserFactory()

    with pytest.raises(ValueError, match="No Transaction found"):
        RazorpayClient().confirm_payment(
            razorpay_order_id=tr.transaction_order_id,
            razorpay_payment_id="pay_test123",
            razorpay_signature="whatever",
            user=stranger,
        )
