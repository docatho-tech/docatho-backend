"""What a customer may and may not do to their own order's status."""

import pytest

from docatho_backend.orders.models import Order
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_customer_cannot_mark_their_own_order_delivered(auth_client):
    """`delivered` is revenue: analytics books a provider earning off it.

    A customer who could set it would have the platform owing the pharmacy
    cash for a COD order nobody ever collected.
    """
    user = UserFactory()
    order = OrderFactory(
        user=user,
        status=Order.Status.PLACED,
        payment_method=Order.PaymentMethod.COD,
    )

    resp = auth_client(user).patch(
        f"/api/orders/{order.pk}/update-status/",
        {"status": "delivered"},
        format="json",
    )

    assert resp.status_code == 400, resp.data
    order.refresh_from_db()
    assert order.status == Order.Status.PLACED
    assert order.delivered_at is None


def test_customer_can_cancel_before_dispatch_but_not_after(auth_client):
    user = UserFactory()
    client = auth_client(user)

    cancellable = OrderFactory(user=user, status=Order.Status.PLACED)
    resp = client.patch(
        f"/api/orders/{cancellable.pk}/update-status/",
        {"status": "cancelled"},
        format="json",
    )
    assert resp.status_code == 200, resp.data
    cancellable.refresh_from_db()
    assert cancellable.status == Order.Status.CANCELLED

    dispatched = OrderFactory(user=user, status=Order.Status.OUT_FOR_DELIVERY)
    resp = client.patch(
        f"/api/orders/{dispatched.pk}/update-status/",
        {"status": "cancelled"},
        format="json",
    )
    assert resp.status_code == 400, resp.data
    dispatched.refresh_from_db()
    assert dispatched.status == Order.Status.OUT_FOR_DELIVERY
