"""The e-prescription a doctor issues after a consultation.

The checks worth having are the ones that decide whether a document is valid
and who it belongs to: it cannot be written before the consultation, it cannot
be written by another doctor, it is one document per appointment, and the
patient can read their own.
"""

from datetime import date
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPrescription
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import PrescriptionSource
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _url(appointment_id, *, admin=False):
    scope = "admin" if admin else "provider"
    return f"/api/healthcare/{scope}/appointments/{appointment_id}/prescription/"


def _appointment(doctor, status=AppointmentStatus.COMPLETED):
    return Appointment.objects.create(
        patient=UserFactory(name="Aarav Sharma", dob=date(1994, 5, 2), gender="male"),
        doctor=doctor,
        scheduled_at=timezone.now() - timedelta(hours=1),
        consultation_mode="online",
        status=status,
        fee=Decimal("500.00"),
    )


FORM = {
    "bp": "120/80",
    "pulse": "72",
    "complaints": "Fever for three days",
    "diagnosis": "Viral fever",
    "advice": "Rest and fluids",
    "medications": [
        {
            "type": "Tab",
            "drug": "Paracetamol 500mg",
            "dose": "1-0-1",
            "duration": "5 days",
        },
        {"type": "", "drug": "", "dose": "", "duration": ""},
    ],
    "follow_up": "After 1 week",
}


def test_doctor_generates_a_prescription_and_gets_a_pdf(auth_client):
    doctor = DoctorProfileFactory(medical_reg_no="TSMC/12345")
    appointment = _appointment(doctor)

    response = auth_client(doctor.provider.user).post(
        _url(appointment.pk), FORM, format="json",
    )

    assert response.status_code == 201
    body = response.data
    assert body["source"] == PrescriptionSource.GENERATED
    assert body["document_url"].endswith(".pdf")
    # The empty row the form always carries is dropped, not stored.
    assert body["medications"] == [FORM["medications"][0]]

    record = AppointmentPrescription.objects.get(appointment=appointment)
    assert record.issued_by == doctor.provider.user
    with record.pdf.open("rb") as handle:
        assert handle.read(5) == b"%PDF-"


def test_editing_replaces_the_document_rather_than_issuing_a_second(auth_client):
    doctor = DoctorProfileFactory()
    appointment = _appointment(doctor)
    client = auth_client(doctor.provider.user)
    client.post(_url(appointment.pk), FORM, format="json")

    corrected = client.post(
        _url(appointment.pk), {"diagnosis": "Dengue"}, format="json",
    )

    assert corrected.status_code == 200
    assert AppointmentPrescription.objects.filter(appointment=appointment).count() == 1
    assert corrected.data["diagnosis"] == "Dengue"
    # The rest of the form survives a partial correction.
    assert corrected.data["bp"] == "120/80"


def test_a_prescription_cannot_be_written_before_the_consultation(auth_client):
    doctor = DoctorProfileFactory()
    appointment = _appointment(doctor, status=AppointmentStatus.CONFIRMED)

    response = auth_client(doctor.provider.user).post(
        _url(appointment.pk), FORM, format="json",
    )

    assert response.status_code == 400
    assert not AppointmentPrescription.objects.exists()


def test_another_doctor_cannot_see_or_write_this_prescription(auth_client):
    doctor = DoctorProfileFactory()
    appointment = _appointment(doctor)
    auth_client(doctor.provider.user).post(_url(appointment.pk), FORM, format="json")

    stranger = auth_client(DoctorProfileFactory().provider.user)

    assert stranger.get(_url(appointment.pk)).status_code == 404
    assert stranger.post(_url(appointment.pk), FORM, format="json").status_code == 404


def test_an_admin_can_upload_the_sheet_the_doctor_wrote_by_hand(auth_client):
    doctor = DoctorProfileFactory()
    appointment = _appointment(doctor)
    auth_client(doctor.provider.user).post(_url(appointment.pk), FORM, format="json")

    response = auth_client(UserFactory(is_staff=True)).post(
        _url(appointment.pk, admin=True),
        {"file_url": "/media/uploads/rx-scan.pdf"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["source"] == PrescriptionSource.UPLOADED
    assert response.data["document_url"] == "/media/uploads/rx-scan.pdf"
    # One document per consultation: the rendered PDF is gone.
    assert not AppointmentPrescription.objects.get(appointment=appointment).pdf


def test_the_patient_reads_the_prescription_off_their_appointment(auth_client):
    doctor = DoctorProfileFactory()
    appointment = _appointment(doctor)
    auth_client(doctor.provider.user).post(_url(appointment.pk), FORM, format="json")

    detail = auth_client(appointment.patient).get(
        f"/api/healthcare/appointments/{appointment.pk}/",
    ).data

    assert detail["prescription"]["diagnosis"] == "Viral fever"
    assert detail["prescription"]["document_url"]
    # …but cannot write one.
    assert auth_client(appointment.patient).post(
        _url(appointment.pk), FORM, format="json",
    ).status_code == 404
