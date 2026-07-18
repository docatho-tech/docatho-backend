"""Cart operations: add (no double-count), update, remove, clear, Rx exposure."""

from decimal import Decimal

import pytest

from docatho_backend.cart.models import Cart
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import RxMedicineFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_add_item_first_add_does_not_double_count(auth_client):
    user = UserFactory()
    med = MedicineFactory()
    client = auth_client(user)
    resp = client.post(
        "/api/cart/add/",
        {"medicine_id": med.id, "quantity": 1},
        format="json",
    )
    assert resp.status_code == 200
    cart = Cart.objects.get(user=user)
    assert cart.items.get(medicine=med).quantity == 1  # not 2


def test_add_item_accumulates_on_repeat(auth_client):
    user = UserFactory()
    med = MedicineFactory()
    client = auth_client(user)
    client.post("/api/cart/add/", {"medicine_id": med.id, "quantity": 2}, format="json")
    client.post("/api/cart/add/", {"medicine_id": med.id, "quantity": 3}, format="json")
    cart = Cart.objects.get(user=user)
    assert cart.items.get(medicine=med).quantity == 5


def test_update_and_remove_and_clear(auth_client):
    user = UserFactory()
    med = MedicineFactory()
    client = auth_client(user)
    client.post("/api/cart/add/", {"medicine_id": med.id, "quantity": 2}, format="json")

    client.patch(
        "/api/cart/update-item/",
        {"medicine_id": med.id, "quantity": 4},
        format="json",
    )
    assert Cart.objects.get(user=user).items.get(medicine=med).quantity == 4

    client.post("/api/cart/remove-item/", {"medicine_id": med.id}, format="json")
    assert Cart.objects.get(user=user).items.count() == 0

    client.post("/api/cart/add/", {"medicine_id": med.id, "quantity": 1}, format="json")
    resp = client.post("/api/cart/clear/", format="json")
    assert resp.status_code == 200
    assert Cart.objects.get(user=user).items.count() == 0


def test_cart_totals(auth_client):
    user = UserFactory()
    med = MedicineFactory(price=Decimal("30.00"), mrp=Decimal("40.00"))
    client = auth_client(user)
    client.post("/api/cart/add/", {"medicine_id": med.id, "quantity": 3}, format="json")
    resp = client.get("/api/cart/")
    assert Decimal(resp.data["subtotal"]) == Decimal("90.00")
    assert Decimal(resp.data["total_mrp"]) == Decimal("120.00")


def test_cart_item_exposes_prescription_flag(auth_client):
    user = UserFactory()
    med = RxMedicineFactory()
    client = auth_client(user)
    client.post("/api/cart/add/", {"medicine_id": med.id, "quantity": 1}, format="json")
    resp = client.get("/api/cart/")
    item = resp.data["items"][0]
    assert item["medicine"]["is_prescription_required"] is True


def test_cart_requires_auth(api_client):
    resp = api_client.get("/api/cart/")
    assert resp.status_code in (401, 403)
