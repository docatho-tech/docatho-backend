"""Address CRUD and single-default enforcement."""

import pytest

from docatho_backend.users.models import Address
from docatho_backend.testing.factories import AddressFactory, UserFactory

pytestmark = pytest.mark.django_db


def _payload(**over):
    base = {
        "address_line1": "12 Main St",
        "city": "Pune",
        "state": "MH",
        "postal_code": "411001",
        "country": "India",
    }
    base.update(over)
    return base


def test_create_list_update_delete(auth_client):
    user = UserFactory()
    client = auth_client(user)

    resp = client.post("/api/addresses/", _payload(), format="json")
    assert resp.status_code == 201
    addr_id = resp.data["id"]

    resp = client.get("/api/addresses/")
    assert resp.status_code == 200
    assert len(resp.data) == 1

    resp = client.patch(f"/api/addresses/{addr_id}/", {"city": "Delhi"}, format="json")
    assert resp.status_code == 200
    assert resp.data["city"] == "Delhi"

    resp = client.delete(f"/api/addresses/{addr_id}/")
    assert resp.status_code == 204
    assert Address.objects.filter(user=user).count() == 0


def test_setting_default_unsets_previous_default(auth_client):
    user = UserFactory()
    client = auth_client(user)
    client.post("/api/addresses/", _payload(is_default=True), format="json")
    resp2 = client.post("/api/addresses/", _payload(is_default=True), format="json")

    defaults = Address.objects.filter(user=user, is_default=True)
    assert defaults.count() == 1
    assert defaults.first().id == resp2.data["id"]


def test_cannot_touch_other_users_address(auth_client):
    owner = UserFactory()
    other = AddressFactory()  # belongs to a different user
    client = auth_client(owner)
    resp = client.patch(f"/api/addresses/{other.id}/", {"city": "X"}, format="json")
    assert resp.status_code == 404


def test_user_address_property_prefers_default():
    user = UserFactory()
    AddressFactory(user=user, is_default=False)
    default = AddressFactory(user=user, is_default=True)
    assert user.address.id == default.id
