"""The public diagnostic-centre list behind the patient app's Tests screens."""

from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from docatho_backend.providers.models import OnboardingStatus
from docatho_backend.testing.factories import DiagnosticTestFactory
from docatho_backend.testing.factories import ProviderFactory

pytestmark = pytest.mark.django_db

URL = "/api/healthcare/diagnostic-centres/"

# Hyderabad, roughly: Kukatpally and Ameerpet are about 8km apart as the crow
# flies (0.0475 deg of latitude and 0.057 of longitude).
KUKATPALLY = (17.4849, 78.3917)
AMEERPET = (17.4374, 78.4487)


def centre(**kwargs):
    kwargs.setdefault("provider_type", "Diagnostic Center")
    kwargs.setdefault("onboarding_status", OnboardingStatus.APPROVED)
    return ProviderFactory(**kwargs)


def results(response):
    return response.data.get("results", response.data)


def test_only_approved_diagnostic_partners_are_listed():
    live = centre(name="True Diagnostics")
    centre(name="Invited Labs", onboarding_status=OnboardingStatus.INVITED)
    centre(name="City Lab", provider_type="Lab")
    ProviderFactory(name="Corner Chemist", provider_type="Chemist")

    rows = results(APIClient().get(URL))
    names = {row["name"] for row in rows}

    # A Lab and a Diagnostic Center are the same thing to a patient; a chemist
    # and a partner still in onboarding are not.
    assert names == {"True Diagnostics", "City Lab"}
    assert next(r for r in rows if r["id"] == live.id)["is_verified"] is True


def test_discount_badge_reflects_the_centres_own_tests():
    discounted = centre(name="True Diagnostics")
    DiagnosticTestFactory(
        provider=discounted,
        price=Decimal("880.00"),
        mrp=Decimal("1000.00"),
    )
    DiagnosticTestFactory(provider=discounted, price=Decimal("500.00"), mrp=None)
    flat = centre(name="HealthMark")
    DiagnosticTestFactory(provider=flat, price=Decimal("500.00"), mrp=None)

    rows = {row["id"]: row for row in results(APIClient().get(URL))}

    assert rows[discounted.id]["max_discount_percent"] == 12
    assert rows[discounted.id]["test_count"] == 2
    # Nothing discounted means no badge at all, not "Upto 0% off".
    assert rows[flat.id]["max_discount_percent"] is None
    assert rows[flat.id]["test_count"] == 1


def test_distance_is_measured_filtered_and_ordered():
    near = centre(name="Near Lab", latitude=KUKATPALLY[0], longitude=KUKATPALLY[1])
    far = centre(name="Far Lab", latitude=AMEERPET[0], longitude=AMEERPET[1])
    unplaced = centre(name="Unplaced Lab")

    here = f"lat={KUKATPALLY[0]}&lng={KUKATPALLY[1]}"
    rows = {row["id"]: row for row in results(APIClient().get(f"{URL}?{here}"))}

    assert rows[near.id]["distance_km"] == 0.0
    assert 7.5 < rows[far.id]["distance_km"] < 8.5
    assert rows[unplaced.id]["distance_km"] is None

    within = results(APIClient().get(f"{URL}?{here}&within_km=1"))
    # A centre with no coordinates is unknown-distance, not nearby.
    assert [row["id"] for row in within] == [near.id]

    ordered = results(APIClient().get(f"{URL}?{here}&ordering=distance"))
    assert [row["id"] for row in ordered][:2] == [near.id, far.id]


def test_distance_ordering_without_coordinates_is_not_an_error():
    """The chip can be tapped before location permission is granted."""
    centre(name="Some Lab")

    response = APIClient().get(f"{URL}?ordering=distance")

    assert response.status_code == 200
    assert results(response)[0]["distance_km"] is None


def test_rating_filter_and_bad_numbers():
    good = centre(name="Four Star", rating_avg=Decimal("4.10"))
    centre(name="Three Star", rating_avg=Decimal("3.20"))

    rows = results(APIClient().get(f"{URL}?min_rating=4"))
    assert [row["id"] for row in rows] == [good.id]

    assert APIClient().get(f"{URL}?min_rating=four").status_code == 400


def test_detail_exposes_the_centre_but_not_its_payout_details():
    live = centre(name="True Diagnostics", bank_ifsc="HDFC0000123")

    response = APIClient().get(f"{URL}{live.id}/")

    assert response.status_code == 200
    assert response.data["name"] == "True Diagnostics"
    assert "bank_ifsc" not in response.data
    assert "commission_percent" not in response.data


def test_tests_expose_turnaround_home_sample_and_code():
    lab = centre(name="HealthMark")
    DiagnosticTestFactory(
        provider=lab,
        code="LAB-CT",
        turnaround="1 day",
        home_sample_available=True,
    )

    rows = results(
        APIClient().get(f"/api/healthcare/diagnostic-tests/?provider={lab.id}"),
    )

    assert rows[0]["code"] == "LAB-CT"
    assert rows[0]["turnaround"] == "1 day"
    assert rows[0]["home_sample_available"] is True
