"""Address CRUD and single-default enforcement."""

import pytest

from docatho_backend.testing.factories import AddressFactory
from docatho_backend.testing.factories import UserFactory
from docatho_backend.users.models import Address

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


def test_coordinates_round_trip_and_stay_optional(auth_client):
    """A pin saves and reads back; an address without one keeps null, not 0."""
    client = auth_client(UserFactory())

    pinned = client.post(
        "/api/addresses/",
        _payload(latitude="21.262035", longitude="81.675567"),
        format="json",
    )
    assert pinned.status_code == 201
    assert pinned.data["latitude"] == "21.262035"
    assert pinned.data["longitude"] == "81.675567"

    # The app sends null for an address typed by hand — "" would be a 400.
    typed = client.post(
        "/api/addresses/",
        _payload(latitude=None, longitude=None),
        format="json",
    )
    assert typed.status_code == 201
    assert typed.data["latitude"] is None
    assert Address.objects.get(pk=typed.data["id"]).latitude is None

    # And a pin can be dropped on an address that was saved without one.
    moved = client.patch(
        f"/api/addresses/{typed.data['id']}/",
        {"latitude": "21.239830", "longitude": "81.667994"},
        format="json",
    )
    assert moved.status_code == 200
    assert moved.data["latitude"] == "21.239830"
