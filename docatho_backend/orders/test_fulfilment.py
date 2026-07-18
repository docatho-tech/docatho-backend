"""Order lifecycle, provider assignment, stock release, and permissions."""

import pytest

from docatho_backend.orders.models import Order
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import OrderItemFactory
from docatho_backend.testing.factories import ProviderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_new_lifecycle_statuses_are_valid():
    order = OrderFactory()
    for st in ("approved", "rejected", "packed"):
        order.update_status(st)
        order.refresh_from_db()
        assert order.status == st


def test_invalid_status_raises():
    order = OrderFactory()
    with pytest.raises(ValueError):
        order.update_status("teleported")


def test_reserve_and_release_stock_roundtrip():
    med = MedicineFactory(stock=10)
    order = OrderFactory()
    OrderItemFactory(order=order, medicine=med, quantity=3)

    order.reserve_stock()
    med.refresh_from_db()
    assert med.stock == 7
    assert order.stock_reserved is True

    # rejecting the order returns stock
    order.update_status(Order.Status.REJECTED)
    med.refresh_from_db()
    assert med.stock == 10
    order.refresh_from_db()
    assert order.stock_reserved is False


def test_reserve_stock_insufficient_raises_and_deducts_nothing():
    med = MedicineFactory(stock=1)
    order = OrderFactory()
    OrderItemFactory(order=order, medicine=med, quantity=5)
    with pytest.raises(ValueError):
        order.reserve_stock()
    med.refresh_from_db()
    assert med.stock == 1


def test_admin_can_assign_provider(auth_client):
    admin = AdminUserFactory()
    provider = ProviderFactory()
    order = OrderFactory()
    client = auth_client(admin)

    resp = client.patch(
        f"/api/admin/orders/{order.id}/assign-provider/",
        {"provider_id": provider.id},
        format="json",
    )
    assert resp.status_code == 200, resp.data
    order.refresh_from_db()
    assert order.assigned_provider_id == provider.id
    # provider is notified
    assert provider.user.notifications.filter(order=order).exists()


def test_admin_order_list_forbidden_for_non_staff(auth_client):
    order = OrderFactory()  # noqa: F841
    client = auth_client(UserFactory())
    resp = client.get("/api/admin/orders/")
    assert resp.status_code == 403


def test_customer_only_sees_own_orders(auth_client):
    mine = OrderFactory()
    OrderFactory()  # someone else's
    client = auth_client(mine.user)
    resp = client.get("/api/orders/")
    assert resp.status_code == 200
    numbers = {o["order_number"] for o in resp.data}
    assert numbers == {mine.order_number}


def test_admin_status_update_notifies_customer(auth_client):
    admin = AdminUserFactory()
    order = OrderFactory()
    client = auth_client(admin)
    resp = client.patch(
        f"/api/admin/orders/{order.id}/update-status/",
        {"status": "out_for_delivery"},
        format="json",
    )
    assert resp.status_code == 200
    assert order.user.notifications.filter(
        notification_type="out_for_delivery",
    ).exists()
