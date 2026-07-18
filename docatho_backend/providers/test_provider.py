"""Provider order queue scoping, status updates, earnings and bank details."""

from decimal import Decimal

import pytest

from docatho_backend.orders.models import Order
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import ProviderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_order_queue_scoped_to_assigned_provider(auth_client):
    provider = ProviderFactory()
    mine = OrderFactory(assigned_provider=provider)
    OrderFactory()  # unassigned / other provider
    resp = auth_client(provider.user).get("/api/providers/chemist-order-list/")
    assert resp.status_code == 200
    numbers = {o["order_number"] for o in resp.data["results"]}
    assert numbers == {mine.order_number}


def test_non_provider_cannot_access_queue(auth_client):
    resp = auth_client(UserFactory()).get("/api/providers/chemist-order-list/")
    assert resp.status_code == 403


def test_provider_can_approve_assigned_order(auth_client):
    provider = ProviderFactory()
    order = OrderFactory(assigned_provider=provider)
    resp = auth_client(provider.user).patch(
        f"/api/providers/chemist-order-update/{order.id}/",
        {"status": "approved"},
        format="json",
    )
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.status == "approved"


def test_provider_cannot_update_unassigned_order(auth_client):
    provider = ProviderFactory()
    other = OrderFactory()  # not assigned to this provider
    resp = auth_client(provider.user).patch(
        f"/api/providers/chemist-order-update/{other.id}/",
        {"status": "approved"},
        format="json",
    )
    assert resp.status_code == 404


def test_provider_cannot_set_disallowed_status(auth_client):
    provider = ProviderFactory()
    order = OrderFactory(assigned_provider=provider)
    resp = auth_client(provider.user).patch(
        f"/api/providers/chemist-order-update/{order.id}/",
        {"status": "returned"},
        format="json",
    )
    assert resp.status_code == 400


def test_provider_earnings(auth_client):
    provider = ProviderFactory()
    o = OrderFactory(
        assigned_provider=provider,
        payment_status=Order.PaymentStatus.PAID,
    )
    Order.objects.filter(pk=o.pk).update(
        total=Decimal("100.00"),
        provider_earning=Decimal("90.00"),
    )
    resp = auth_client(provider.user).get("/api/providers/earnings/")
    assert resp.status_code == 200
    assert Decimal(resp.data["payout"]) == Decimal("90.00")


def test_provider_bank_update(auth_client):
    provider = ProviderFactory()
    resp = auth_client(provider.user).patch(
        "/api/providers/bank/",
        {"bank_account_number": "1234567890", "bank_ifsc": "HDFC0001"},
        format="json",
    )
    assert resp.status_code == 200
    provider.refresh_from_db()
    assert provider.bank_account_number == "1234567890"
