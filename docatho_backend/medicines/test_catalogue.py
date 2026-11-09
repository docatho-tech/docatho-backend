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


def _names(resp):
    results = resp.data["results"] if "results" in resp.data else resp.data
    return [m["name"] for m in results]


def test_price_range_and_sort_are_numeric_not_lexicographic(api_client):
    """`price` is a Decimal, so 9 sorts below 11 rather than above it.

    The filter screen's price range and the "Low to High" sort both lean on
    this; a CharField price would pass a naive "is it sorted" check and still
    order 100 before 9.
    """
    MedicineFactory(name="Cheap", price="9.00")
    MedicineFactory(name="Mid", price="11.00")
    MedicineFactory(name="Dear", price="100.00")

    assert _names(api_client.get("/api/medicines/?ordering=price")) == [
        "Cheap",
        "Mid",
        "Dear",
    ]
    assert _names(api_client.get("/api/medicines/?ordering=-price")) == [
        "Dear",
        "Mid",
        "Cheap",
    ]

    ranged = _names(api_client.get("/api/medicines/?price_min=10&price_max=50"))
    assert ranged == ["Mid"]


def test_in_stock_filter_splits_on_zero(api_client):
    MedicineFactory(name="Stocked", stock=4)
    MedicineFactory(name="Empty", stock=0)

    assert _names(api_client.get("/api/medicines/?in_stock=true")) == ["Stocked"]
    assert _names(api_client.get("/api/medicines/?in_stock=false")) == ["Empty"]
    # Absent means "do not filter", not "false".
    assert sorted(_names(api_client.get("/api/medicines/"))) == ["Empty", "Stocked"]


def test_schedule_in_accepts_several_schedules(api_client):
    MedicineFactory(name="Hdrug", schedule=DrugSchedule.H)
    MedicineFactory(name="H1drug", schedule=DrugSchedule.H1)
    MedicineFactory(name="Otc", schedule=DrugSchedule.OTC)

    got = sorted(_names(api_client.get("/api/medicines/?schedule_in=H,H1")))
    assert got == ["H1drug", "Hdrug"]
