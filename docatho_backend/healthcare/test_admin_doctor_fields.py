"""The admin doctor endpoint's newly writable fields, and the specialty sync."""

import pytest
from rest_framework.test import APIClient

from docatho_backend.healthcare.models import DoctorProfile
from docatho_backend.healthcare.models import MedicalSpecialty
from docatho_backend.providers.enums import ProviderType
from docatho_backend.providers.models import Provider
from docatho_backend.users.models import User


@pytest.fixture
def admin_client(db) -> APIClient:
    admin = User.objects.create_user(phone="+919000000010", password="pw")
    admin.is_staff = True
    admin.save()
    client = APIClient()
    client.force_authenticate(user=admin)
    return client


@pytest.fixture
def doctor(db) -> DoctorProfile:
    user = User.objects.create_user(phone="+919000000011", password="pw")
    provider = Provider.objects.create(
        name="Asha Rao",
        specialty="Cardiologist",
        user=user,
        provider_type=ProviderType.DOCTOR.value,
    )
    return DoctorProfile.objects.create(provider=provider)


def url_for(doctor: DoctorProfile) -> str:
    return f"/api/healthcare/admin/doctors/{doctor.id}/"


def test_setting_specialties_rewrites_the_providers_label(admin_client, doctor):
    """The apps render `provider.specialty`; the filter matches the M2M.

    Left unsynced, a doctor filed under Dermatologist keeps displaying
    "Cardiologist" on their card forever.
    """
    derm = MedicalSpecialty.objects.create(name="Dermatologist")

    response = admin_client.patch(
        url_for(doctor), {"specialty_ids": [derm.id]}, format="json",
    )

    assert response.status_code == 200, response.data
    doctor.provider.refresh_from_db()
    assert doctor.provider.specialty == "Dermatologist"


def test_several_specialties_are_joined_into_the_label(admin_client, doctor):
    first = MedicalSpecialty.objects.create(name="Cardiologist")
    second = MedicalSpecialty.objects.create(name="Neurologist")

    admin_client.patch(
        url_for(doctor), {"specialty_ids": [first.id, second.id]}, format="json",
    )

    doctor.provider.refresh_from_db()
    assert doctor.provider.specialty == "Cardiologist, Neurologist"


def test_an_edit_that_does_not_touch_specialties_leaves_the_label_alone(
    admin_client, doctor,
):
    """Clearing the label off an unrelated edit would blank every doctor card."""
    admin_client.patch(url_for(doctor), {"clinic_city": "Raipur"}, format="json")

    doctor.provider.refresh_from_db()
    assert doctor.provider.specialty == "Cardiologist"


def test_clinic_images_and_coordinates_are_writable(admin_client, doctor):
    """All three are in the public serializer but were absent from the admin one.

    They could be read by patients and set by nobody, so they stayed empty.
    """
    response = admin_client.patch(
        url_for(doctor),
        {
            "clinic_images": ["https://example.com/a.png"],
            "clinic_latitude": "21.250000",
            "clinic_longitude": "81.629997",
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    doctor.refresh_from_db()
    assert doctor.clinic_images == ["https://example.com/a.png"]
    assert str(doctor.clinic_latitude) == "21.250000"


def test_the_verification_documents_are_readable(admin_client, doctor):
    """Approve/Reject is a judgement on these; they were on no serializer."""
    response = admin_client.get(url_for(doctor))

    assert response.status_code == 200
    assert "license_document" in response.data
    assert "degree_document" in response.data


def test_the_verification_documents_are_writable(admin_client, doctor):
    """Onboarding is done by an admin on the doctor's behalf.

    Nothing ever wrote these — no endpoint accepted an upload — so a doctor was
    approved with no licence on file at all.
    """
    response = admin_client.patch(
        url_for(doctor),
        {
            "license_document": "https://example.com/licence.pdf",
            "degree_document": "https://example.com/degree.pdf",
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    doctor.refresh_from_db()
    assert doctor.license_document == "https://example.com/licence.pdf"
    assert doctor.degree_document == "https://example.com/degree.pdf"


def test_qualifications_are_listable_and_admin_writable(admin_client):
    created = admin_client.post(
        "/api/healthcare/qualifications/", {"name": "MBBS"}, format="json",
    )
    assert created.status_code == 201, created.data

    listed = APIClient().get("/api/healthcare/qualifications/")
    assert listed.status_code == 200
    assert [row["name"] for row in listed.data["results"]] == ["MBBS"]


def test_a_non_admin_cannot_add_a_qualification(db):
    user = User.objects.create_user(phone="+919000000012", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/healthcare/qualifications/", {"name": "MBBS"}, format="json",
    )

    assert response.status_code == 403


def test_the_phone_number_is_editable(admin_client, doctor):
    response = admin_client.patch(
        url_for(doctor), {"phone": "+919000009999"}, format="json",
    )

    assert response.status_code == 200, response.data
    doctor.provider.user.refresh_from_db()
    assert str(doctor.provider.user.phone) == "+919000009999"


def test_a_phone_another_account_uses_is_refused(admin_client, doctor):
    """`User.phone` is the login field and has no unique constraint.

    `AdminLoginView` does `User.objects.get(phone=...)`, so a duplicate makes
    that query raise MultipleObjectsReturned — locking both accounts out.
    """
    User.objects.create_user(phone="+919000008888", password="pw")

    response = admin_client.patch(
        url_for(doctor), {"phone": "+919000008888"}, format="json",
    )

    assert response.status_code == 400, response.data
    doctor.provider.user.refresh_from_db()
    assert str(doctor.provider.user.phone) == "+919000000011"


def test_resaving_a_doctors_own_number_is_allowed(admin_client, doctor):
    """The clash check must exclude the record being edited."""
    response = admin_client.patch(
        url_for(doctor),
        {"phone": "+919000000011", "clinic_city": "Raipur"},
        format="json",
    )

    assert response.status_code == 200, response.data
