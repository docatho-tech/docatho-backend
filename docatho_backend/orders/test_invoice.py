"""Invoice PDF generation and download (EP-03)."""

from decimal import Decimal

import pytest

from docatho_backend.orders.invoices import get_or_create_invoice
from docatho_backend.orders.models import Invoice
from docatho_backend.testing.factories import AddressFactory
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import OrderItemFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _order_with_items(user):
    address = AddressFactory(user=user)
    order = OrderFactory(user=user, address=address)
    OrderItemFactory(
        order=order, medicine=MedicineFactory(), quantity=2, unit_price=Decimal("25.00"),
    )
    order.recalc_totals()
    return order


def test_get_or_create_invoice_generates_pdf():
    user = UserFactory()
    order = _order_with_items(user)
    invoice = get_or_create_invoice(order)
    assert isinstance(invoice, Invoice)
    assert invoice.pdf.name.endswith(".pdf")
    with invoice.pdf.open("rb") as fh:
        assert fh.read(4) == b"%PDF"


def test_invoice_is_idempotent():
    user = UserFactory()
    order = _order_with_items(user)
    first = get_or_create_invoice(order)
    second = get_or_create_invoice(order)
    assert first.pk == second.pk
    assert Invoice.objects.filter(order=order).count() == 1


def test_download_invoice_endpoint(auth_client):
    user = UserFactory()
    order = _order_with_items(user)
    resp = auth_client(user).get(f"/api/orders/{order.id}/invoice/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"


def test_cannot_download_other_users_invoice(auth_client):
    owner = UserFactory()
    order = _order_with_items(owner)
    resp = auth_client(UserFactory()).get(f"/api/orders/{order.id}/invoice/")
    assert resp.status_code == 404
