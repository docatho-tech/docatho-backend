"""Catalogue: browse/search/filter, Rx auto-flagging, admin-only writes."""

import pytest

from docatho_backend.medicines.models import DrugSchedule
from docatho_backend.medicines.models import Medicine
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import CategoryFactory
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_scheduled_drug_forces_prescription_required():
    med = MedicineFactory(schedule=DrugSchedule.H, is_prescription_required=False)
    med.refresh_from_db()
    assert med.is_prescription_required is True


def test_otc_drug_not_prescription_required():
    med = MedicineFactory(schedule=DrugSchedule.OTC, is_prescription_required=False)
    assert med.is_prescription_required is False


def test_catalogue_is_publicly_readable(api_client):
    MedicineFactory(name="Paracetamol")
    resp = api_client.get("/api/medicines/")
    assert resp.status_code == 200


def test_search_medicines_by_name(auth_client):
    MedicineFactory(name="Amoxicillin")
    MedicineFactory(name="Ibuprofen")
    client = auth_client(UserFactory())
    resp = client.get("/api/medicines/?search=Amox")
    results = resp.data["results"] if "results" in resp.data else resp.data
    names = [m["name"] for m in results]
    assert "Amoxicillin" in names
    assert "Ibuprofen" not in names


def test_filter_by_prescription_required(auth_client):
    MedicineFactory(name="OTC med", schedule=DrugSchedule.OTC)
    MedicineFactory(name="Rx med", schedule=DrugSchedule.H)
    client = auth_client(UserFactory())
    resp = client.get("/api/medicines/?is_prescription_required=true")
    results = resp.data["results"] if "results" in resp.data else resp.data
    assert all(m["is_prescription_required"] for m in results)


def test_non_admin_cannot_create_medicine(auth_client):
    client = auth_client(UserFactory())
    resp = client.post(
        "/api/medicines/",
        {"name": "Hack", "price": "1.00"},
        format="json",
    )
    assert resp.status_code == 403


def test_admin_can_create_medicine_with_categories(auth_client):
    admin = AdminUserFactory()
    category = CategoryFactory()
    client = auth_client(admin)
    resp = client.post(
        "/api/medicines/",
        {
            "name": "New Med",
            "price": "42.00",
            "mrp": "50.00",
            "schedule": "OTC",
            "category_ids": [category.id],
        },
        format="json",
    )
    assert resp.status_code == 201, resp.data
    med = Medicine.objects.get(name="New Med")
    assert list(med.category.all()) == [category]


def test_admin_medicine_viewset_requires_staff(auth_client):
    client = auth_client(UserFactory())
    resp = client.get("/api/medicines/list/admin/")
    assert resp.status_code == 403
