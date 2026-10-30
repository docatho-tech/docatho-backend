"""`/api/healthcare/doctors/?specialty=` has to reach unlinked doctors too.

Almost no doctor on production has a row in the `specialties` M2M — onboarding
writes the speciality as free text on `Provider.specialty` instead. While the
filter keyed on the M2M alone, tapping any speciality in the apps returned an
empty list for all but a couple of doctors.
"""

import pytest
from rest_framework.test import APIClient

from docatho_backend.healthcare.models import DoctorProfile
from docatho_backend.healthcare.models import MedicalSpecialty
from docatho_backend.healthcare.models import VerificationStatus
from docatho_backend.providers.enums import ProviderType
from docatho_backend.providers.models import Provider
from docatho_backend.users.models import User

URL = "/api/healthcare/doctors/"


def make_doctor(phone: str, name: str, specialty: str) -> DoctorProfile:
    user = User.objects.create_user(phone=phone, password="pw")
    provider = Provider.objects.create(
        name=name,
        specialty=specialty,
        user=user,
        provider_type=ProviderType.DOCTOR.value,
    )
    return DoctorProfile.objects.create(
        provider=provider,
        is_verified=True,
        verification_status=VerificationStatus.APPROVED,
    )


@pytest.fixture
def cardiology(db) -> MedicalSpecialty:
    return MedicalSpecialty.objects.create(name="Cardiologist")


@pytest.fixture
def linked(db, cardiology) -> DoctorProfile:
    doctor = make_doctor("+919000000021", "Dr. Linked", "Cardiologist")
    doctor.specialties.add(cardiology)
    return doctor


@pytest.fixture
def unlinked(db) -> DoctorProfile:
    """The production shape: the text field is set, the M2M is empty."""
    return make_doctor("+919000000022", "Dr. Unlinked", "Cardiologist")


@pytest.fixture
def other(db) -> DoctorProfile:
    return make_doctor("+919000000023", "Dr. Other", "Dermatologist")


def names(response) -> set[str]:
    return {row["name"] for row in response.json()["results"]}


def test_specialty_id_returns_linked_and_unlinked_doctors(
    linked,
    unlinked,
    other,
    cardiology,
):
    response = APIClient().get(URL, {"specialty": cardiology.id})

    assert response.status_code == 200
    assert names(response) == {"Dr. Linked", "Dr. Unlinked"}


def test_specialty_name_is_accepted_as_well_as_an_id(linked, unlinked, other):
    response = APIClient().get(URL, {"specialty": "Cardiologist"})

    assert response.status_code == 200
    assert names(response) == {"Dr. Linked", "Dr. Unlinked"}


def test_an_unknown_specialty_id_matches_nobody(linked, unlinked, other):
    """A stale id must not fall through to matching every doctor."""
    response = APIClient().get(URL, {"specialty": 999999})

    assert response.status_code == 200
    assert response.json()["results"] == []
