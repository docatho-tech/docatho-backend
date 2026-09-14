"""Admin dashboard period windows and diagnostic booking kind filters."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import DiagnosticBooking
from docatho_backend.healthcare.models import DiagnosticBookingKind
from docatho_backend.healthcare.models import Qualification
from docatho_backend.providers.enums import ProviderType
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import DiagnosticTestFactory
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import ProviderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_dashboard_stats_default_period_and_new_kpis(auth_client):
    admin = AdminUserFactory()
    stats = auth_client(admin).get("/api/healthcare/admin/dashboard-stats/")
    assert stats.status_code == 200
    for key in (
        "period",
        "from",
        "to",
        "total_orders",
        "total_revenue",
        "total_customers",
        "doctor_consultations",
        "lab_tests",
        "lab_revenue",
        "diagnostic_tests",
        "diagnostic_revenue",
        "medicine_orders",
        "lab_bookings_requested",
        "imaging_bookings_requested",
        "home_care_bookings_requested",
        "patients_count",
        "pending_doctor_verifications",
        "appointments_pending_payment",
        "appointments_by_status",
    ):
        assert key in stats.data
    assert stats.data["period"] == "today"


def test_dashboard_stats_week_month_and_custom_windows(auth_client):
    admin = AdminUserFactory()
    client = auth_client(admin)

    week = client.get("/api/healthcare/admin/dashboard-stats/?period=week")
    assert week.status_code == 200
    assert week.data["period"] == "week"

    month = client.get("/api/healthcare/admin/dashboard-stats/?period=month")
    assert month.status_code == 200
    assert month.data["period"] == "month"

    start = (timezone.localdate() - timedelta(days=3)).isoformat()
    end = timezone.localdate().isoformat()
    custom = client.get(
        "/api/healthcare/admin/dashboard-stats/",
        {"period": "custom", "from": start, "to": end},
    )
    assert custom.status_code == 200
    assert custom.data["period"] == "custom"
    assert custom.data["from"] == start
    assert custom.data["to"] == end


def test_admin_diagnostic_bookings_filter_by_kind(auth_client):
    admin = AdminUserFactory()
    patient = UserFactory()
    lab = DiagnosticBooking.objects.create(
        patient=patient,
        kind=DiagnosticBookingKind.LAB,
        total_amount=Decimal("100.00"),
    )
    imaging = DiagnosticBooking.objects.create(
        patient=patient,
        kind=DiagnosticBookingKind.DIAGNOSTIC,
        total_amount=Decimal("200.00"),
    )
    home = DiagnosticBooking.objects.create(
        patient=patient,
        kind=DiagnosticBookingKind.HOME_HEALTHCARE,
        total_amount=Decimal("300.00"),
    )
    client = auth_client(admin)

    labs = client.get("/api/healthcare/admin/diagnostic-bookings/?kind=lab")
    assert labs.status_code == 200
    lab_ids = {row["id"] for row in labs.data.get("results", labs.data)}
    assert lab.id in lab_ids
    assert imaging.id not in lab_ids
    assert home.id not in lab_ids

    diags = client.get("/api/healthcare/admin/diagnostic-bookings/?kind=diagnostic")
    diag_ids = {row["id"] for row in diags.data.get("results", diags.data)}
    assert imaging.id in diag_ids
    assert lab.id not in diag_ids


def test_booking_kind_inferred_from_center_provider_type(auth_client):
    patient = UserFactory()
    center = ProviderFactory(provider_type=ProviderType.DIAGNOSTIC_CENTER.value)
    test = DiagnosticTestFactory(price=Decimal("499.00"))
    resp = auth_client(patient).post(
        "/api/healthcare/diagnostic-bookings/",
        {
            "test_ids": [test.id],
            "center": center.id,
            "patient_address": "12 MG Road",
            "scheduled_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
        },
        format="json",
    )
    assert resp.status_code == 201, resp.data
    assert resp.data["kind"] == DiagnosticBookingKind.DIAGNOSTIC


def test_qualifications_filter_by_level(auth_client):
    admin = AdminUserFactory()
    Qualification.objects.create(name="MBBS", level="ug")
    Qualification.objects.create(name="MD", level="pg")
    resp = auth_client(admin).get("/api/healthcare/qualifications/?level=ug")
    assert resp.status_code == 200
    names = {row["name"] for row in resp.data.get("results", resp.data)}
    assert "MBBS" in names
    assert "MD" not in names


def test_commission_percent_rejects_out_of_range(auth_client):
    admin = AdminUserFactory()
    provider = ProviderFactory()
    resp = auth_client(admin).patch(
        f"/api/providers/admin/{provider.id}/",
        {"commission_percent": "150"},
        format="json",
    )
    assert resp.status_code == 400


def test_compute_commission_uses_assigned_provider_rate():
    provider = ProviderFactory(commission_percent=Decimal("8.00"))
    order = OrderFactory(
        assigned_provider=provider,
        subtotal=Decimal("100.00"),
        total=Decimal("100.00"),
    )
    order.compute_commission()
    order.refresh_from_db()
    assert order.commission_rate == Decimal("8.00")
    assert order.commission_amount == Decimal("8.00")
    assert order.provider_earning == Decimal("92.00")


def test_series_buckets_by_position_in_the_window():
    """The sparkline's eight buckets split the window evenly, ends included.

    A row landing exactly on either boundary has to count once — the totals
    printed beside the sparkline come from the same window, and a bar chart
    that sums to less than the number above it reads as a bug in the number.
    """
    from docatho_backend.healthcare.dashboard_stats import SERIES_BUCKETS
    from docatho_backend.healthcare.dashboard_stats import _series

    start = timezone.now()
    end = start + timedelta(days=8)
    rows = [
        (start, 1),  # first bucket
        (start + timedelta(days=4), 2),  # middle
        (end, 5),  # clamped into the last bucket, not dropped
        (None, 99),  # a null timestamp is skipped, never counted as zero-time
    ]

    series = _series(rows, start, end)

    assert len(series) == SERIES_BUCKETS
    assert series[0] == 1
    assert series[4] == 2
    assert series[-1] == 5
    assert sum(series) == 8


def test_change_is_none_when_there_is_nothing_to_compare_against():
    """Growth from an empty window is a first sale, not "100% up"."""
    from docatho_backend.healthcare.dashboard_stats import _change

    assert _change(10, 0) is None
    assert _change(0, 0) is None
    assert _change(150, 100) == 50.0
    assert _change(50, 100) == -50.0
