"""Insights, Catalog and the My Earnings blocks the partner app draws.

Each persona answers the same questions about different rows, so the checks
here are mostly that the right rows were counted — and that the two places a
partner could be shown someone else's data (the shared test catalogue, another
pharmacy's listing) hold.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import DiagnosticTest
from docatho_backend.medicines.models import Medicine
from docatho_backend.medicines.models import ProviderMedicine
from docatho_backend.orders.models import Order
from docatho_backend.providers.models import Provider
from docatho_backend.testing.factories import DiagnosticTestFactory
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import ProviderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db

INSIGHTS = "/api/providers/insights/"
CATALOG = "/api/providers/catalog/"
EARNINGS = "/api/providers/earnings/"
STATEMENT = "/api/providers/statement/"


def _completed_appointment(doctor, *, days_ago, fee="500.00", platform_fee="100.00"):
    return Appointment.objects.create(
        patient=UserFactory(),
        doctor=doctor,
        scheduled_at=timezone.now() - timedelta(days=days_ago),
        consultation_mode="online",
        status=AppointmentStatus.COMPLETED,
        fee=Decimal(fee),
        platform_fee=Decimal(platform_fee),
    )


def test_doctor_insights_count_only_this_doctors_completed_work(auth_client):
    doctor = DoctorProfileFactory()
    other = DoctorProfileFactory()
    _completed_appointment(doctor, days_ago=1)
    _completed_appointment(doctor, days_ago=2)
    _completed_appointment(other, days_ago=1)
    # Outside the week: it belongs to the dotted comparison line, not the total.
    _completed_appointment(doctor, days_ago=9)

    data = auth_client(doctor.provider.user).get(INSIGHTS).data

    assert data["persona"] == "doctor"
    assert len(data["labels"]) == 7
    metrics = {metric["key"]: metric for metric in data["metrics"]}
    assert [metric["label"] for metric in data["metrics"]] == [
        "Net Earnings",
        "Total Consultations",
    ]
    assert metrics["consultations"]["value"] == 2
    # 500 charged less 100 platform fee, twice.
    assert metrics["net_earnings"]["value"] == 800
    assert len(metrics["net_earnings"]["series"]) == 7
    assert len(metrics["net_earnings"]["previous"]) == 7


def test_pharmacy_insights_average_is_the_period_average(auth_client):
    provider = ProviderFactory()
    for total in ("100.00", "300.00"):
        OrderFactory(
            assigned_provider=provider,
            status=Order.Status.DELIVERED,
            total=Decimal(total),
            provider_earning=Decimal(total),
        )

    data = auth_client(provider.user).get(INSIGHTS).data
    metrics = {metric["key"]: metric for metric in data["metrics"]}

    assert data["persona"] == "pharmacy"
    assert metrics["orders_delivered"]["value"] == 2
    # Both orders landed today, so the daily averages are [0,0,0,0,0,0,200] —
    # summing those would answer 200 by luck. Two orders on different days is
    # what would break a sum, and the period average is 200 either way.
    assert metrics["avg_order_value"]["value"] == 200


def test_lab_catalog_lists_shared_and_own_tests_and_refuses_shared_switches(auth_client):
    lab = ProviderFactory(provider_type="Lab")
    mine = DiagnosticTestFactory(provider=lab, is_active=True)
    shared = DiagnosticTestFactory(provider=None, is_active=True)
    client = auth_client(lab.user)

    data = client.get(CATALOG).data
    assert data["kind"] == "tests"
    assert {item["id"] for item in data["items"]} >= {mine.pk, shared.pk}

    # Switching off a shared row would take the test off every other centre.
    resp = client.patch(
        CATALOG,
        {"items": [{"id": mine.pk, "is_available": False}, {"id": shared.pk, "is_available": False}]},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["updated"] == 1
    mine.refresh_from_db()
    shared.refresh_from_db()
    assert mine.is_active is False
    assert shared.is_active is True


def test_pharmacy_catalog_switch_is_per_pharmacy(auth_client):
    one = ProviderFactory()
    two = ProviderFactory()
    medicine = MedicineFactory()

    resp = auth_client(one.user).patch(
        CATALOG,
        {"items": [{"id": medicine.pk, "is_available": True}]},
        format="json",
    )
    assert resp.status_code == 200
    assert ProviderMedicine.objects.filter(provider=one, medicine=medicine).exists()

    # The other pharmacy has answered nothing, so it stocks nothing yet.
    items = auth_client(two.user).get(CATALOG).data["items"]
    assert all(item["is_available"] is False for item in items)


def test_catalog_patch_rejects_a_malformed_body(auth_client):
    client = auth_client(ProviderFactory().user)
    assert client.patch(CATALOG, {"items": "all"}, format="json").status_code == 400
    assert client.patch(CATALOG, {"items": [{"id": 1}]}, format="json").status_code == 400


def test_earnings_carries_the_week_the_payout_and_the_lines(auth_client):
    doctor = DoctorProfileFactory()
    _completed_appointment(doctor, days_ago=1)
    _completed_appointment(doctor, days_ago=2)

    data = auth_client(doctor.provider.user).get(EARNINGS).data

    assert Decimal(data["week_total"]) == Decimal("800.00")
    assert len(data["lines"]) == 2
    line = data["lines"][0]
    assert Decimal(line["amount"]) - Decimal(line["platform_fee"]) == Decimal(line["net"])
    # No account on file, so there is nowhere for the money to go and the
    # screen must show its amber prompt rather than a scheduled transfer.
    assert data["next_payout"]["has_bank"] is False
    assert data["next_payout"]["status"] == "unscheduled"


def test_earnings_marks_the_payout_scheduled_once_a_bank_is_on_file(auth_client):
    doctor = DoctorProfileFactory()
    _completed_appointment(doctor, days_ago=1)
    Provider.objects.filter(pk=doctor.provider.pk).update(
        bank_account_number="000011112222", bank_ifsc="HDFC0001234",
    )

    payout = auth_client(doctor.provider.user).get(EARNINGS).data["next_payout"]

    assert payout["has_bank"] is True
    assert payout["bank_last4"] == "2222"
    assert payout["bank_name"] == "HDFC"
    assert payout["status"] == "scheduled"


def test_a_repeat_patient_reads_as_a_follow_up(auth_client):
    doctor = DoctorProfileFactory()
    patient = UserFactory()
    for days_ago in (5, 1):
        Appointment.objects.create(
            patient=patient,
            doctor=doctor,
            scheduled_at=timezone.now() - timedelta(days=days_ago),
            consultation_mode="in_clinic",
            status=AppointmentStatus.COMPLETED,
            fee=Decimal("350.00"),
            platform_fee=Decimal("80.00"),
        )

    lines = auth_client(doctor.provider.user).get(EARNINGS).data["lines"]

    assert [line["kind"] for line in lines] == ["Follow up", "Consultation"]


def test_a_medicine_keeps_its_pack_unit_and_code_on_the_catalog(auth_client):
    provider = ProviderFactory()
    MedicineFactory(name="Amoxicillin 500mg", pack_unit="Caps", code="MED-004")

    items = auth_client(provider.user).get(CATALOG).data["items"]
    row = next(item for item in items if item["name"] == "Amoxicillin 500mg")

    assert row["detail"] == "Caps"
    assert row["code"] == "MED-004"


def test_unknown_period_falls_back_to_the_week(auth_client):
    provider = ProviderFactory()
    data = auth_client(provider.user).get(INSIGHTS, {"period": "decade"}).data
    assert data["period"] == "week"
    assert len(data["labels"]) == 7


def test_catalog_and_insights_need_a_provider(api_client):
    assert api_client.get(CATALOG).status_code in (401, 403)
    assert api_client.get(INSIGHTS).status_code in (401, 403)


def test_a_test_catalog_row_carries_its_code_and_turnaround(auth_client):
    lab = ProviderFactory(provider_type="Diagnostic Center")
    DiagnosticTest.objects.filter(provider=lab).delete()
    DiagnosticTestFactory(provider=lab, name="CT Scan", code="LAB-CT", turnaround="24 hours")

    items = auth_client(lab.user).get(CATALOG).data["items"]
    row = next(item for item in items if item["name"] == "CT Scan")

    assert row["code"] == "LAB-CT"
    assert row["meta"] == "TAT 24 hours"


def test_medicine_with_no_listing_shows_the_list_price(auth_client):
    provider = ProviderFactory()
    medicine = MedicineFactory(price=Decimal("120.00"), mrp=Decimal("150.00"))

    items = auth_client(provider.user).get(CATALOG).data["items"]
    row = next(item for item in items if item["id"] == medicine.pk)

    assert Decimal(row["partner_price"]) == Decimal("120.00")
    assert Decimal(row["mrp"]) == Decimal("150.00")
    assert row["is_available"] is False
    assert Medicine.objects.filter(pk=medicine.pk).exists()


def test_custom_range_buckets_the_days_asked_for(auth_client):
    doctor = DoctorProfileFactory()
    _completed_appointment(doctor, days_ago=3)
    _completed_appointment(doctor, days_ago=20)

    today = timezone.localdate()
    data = auth_client(doctor.provider.user).get(
        INSIGHTS,
        {"start": (today - timedelta(days=5)).isoformat(), "end": today.isoformat()},
    ).data

    assert data["period"] == "custom"
    assert data["start"] == (today - timedelta(days=5)).isoformat()
    assert len(data["labels"]) == 6
    metrics = {metric["key"]: metric for metric in data["metrics"]}
    # Only the appointment inside the six days counts; the other is 20 back.
    assert metrics["consultations"]["value"] == 1


def test_a_backwards_or_oversized_range_falls_back_to_the_preset(auth_client):
    doctor = DoctorProfileFactory()
    client = auth_client(doctor.provider.user)
    today = timezone.localdate()

    backwards = client.get(
        INSIGHTS,
        {"start": today.isoformat(), "end": (today - timedelta(days=5)).isoformat()},
    ).data
    assert backwards["period"] == "week"
    assert len(backwards["labels"]) == 7

    huge = client.get(
        INSIGHTS,
        {"start": (today - timedelta(days=400)).isoformat(), "end": today.isoformat()},
    ).data
    assert len(huge["labels"]) == 92


def test_statement_csv_carries_every_line_and_a_total(auth_client):
    doctor = DoctorProfileFactory()
    for days_ago in (1, 2, 3):
        _completed_appointment(doctor, days_ago=days_ago)
    # Outside the week, so it must not be in a week's statement.
    _completed_appointment(doctor, days_ago=10)

    response = auth_client(doctor.provider.user).get(STATEMENT, {"period": "week"})

    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]
    rows = [row for row in response.content.decode().splitlines() if row.strip()]
    assert rows[1].startswith("Date,Type,Name,Mode,Amount,Platform fee,Net pay")
    # Header, column names, three lines, the total.
    assert len(rows) == 6
    assert rows[-1] == "Total,,,,1500.00,300.00,1200.00"


def test_statement_is_not_capped_at_the_screen_s_line_limit(auth_client):
    doctor = DoctorProfileFactory()
    for index in range(55):
        _completed_appointment(doctor, days_ago=index % 7, fee="100.00", platform_fee="10.00")

    response = auth_client(doctor.provider.user).get(STATEMENT, {"period": "week"})
    rows = [row for row in response.content.decode().splitlines() if row.strip()]

    assert len(rows) == 55 + 3
    assert rows[-1] == "Total,,,,5500.00,550.00,4950.00"
