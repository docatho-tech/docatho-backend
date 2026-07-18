"""Revenue, sales and payout analytics (EP-11 / EP-12)."""

from decimal import Decimal

import pytest

from docatho_backend.orders.models import Order
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import OrderItemFactory
from docatho_backend.testing.factories import ProviderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _paid_order(provider=None, total="100.00", commission="10.00", earning="90.00"):
    order = OrderFactory(
        payment_status=Order.PaymentStatus.PAID,
        assigned_provider=provider,
    )
    Order.objects.filter(pk=order.pk).update(
        total=Decimal(total),
        subtotal=Decimal(total),
        commission_amount=Decimal(commission),
        provider_earning=Decimal(earning),
    )
    order.refresh_from_db()
    return order


def test_revenue_summary_admin_only(auth_client):
    client = auth_client(UserFactory())
    assert client.get("/api/analytics/revenue/").status_code == 403


def test_revenue_summary_totals(auth_client):
    admin = AdminUserFactory()
    _paid_order(total="100.00", commission="10.00", earning="90.00")
    _paid_order(total="50.00", commission="5.00", earning="45.00")
    # an unpaid order must NOT count
    OrderFactory(payment_status=Order.PaymentStatus.PENDING)

    resp = auth_client(admin).get("/api/analytics/revenue/")
    assert resp.status_code == 200
    assert Decimal(resp.data["total_revenue"]) == Decimal("150.00")
    assert Decimal(resp.data["total_commission"]) == Decimal("15.00")
    assert resp.data["total_orders"] == 2


def test_sales_analytics_top_products(auth_client):
    admin = AdminUserFactory()
    med = MedicineFactory(name="TopSeller", price=Decimal("20.00"))
    order = _paid_order()
    OrderItemFactory(order=order, medicine=med, quantity=5, unit_price=Decimal("20.00"))

    resp = auth_client(admin).get("/api/analytics/sales/")
    assert resp.status_code == 200
    names = [p["medicine__name"] for p in resp.data["top_products"]]
    assert "TopSeller" in names


def test_provider_payout_summary(auth_client):
    admin = AdminUserFactory()
    provider = ProviderFactory()
    _paid_order(provider=provider, earning="90.00")
    _paid_order(provider=provider, earning="45.00")

    resp = auth_client(admin).get("/api/analytics/payouts/")
    assert resp.status_code == 200
    row = next(
        r for r in resp.data["providers"] if r["assigned_provider_id"] == provider.id
    )
    assert Decimal(row["payout"]) == Decimal("135.00")


def test_revenue_export_csv(auth_client):
    admin = AdminUserFactory()
    _paid_order()
    resp = auth_client(admin).get("/api/analytics/revenue/export/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert b"order_number" in resp.content
