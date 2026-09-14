"""The partner app's own endpoints: a centre's booking queue, the appointment
detail the doctor's screen reads, and the deadline that answers a request when
nobody else does.

These are the surfaces the provider app was rebuilt against. Before them a
diagnostic centre had no API of its own at all — the only booking endpoints were
the patient's and the admin's, neither of which a partner login can reach.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import DiagnosticBooking
from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.testing.factories import DiagnosticTestFactory
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import ProviderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db

BOOKINGS_URL = "/api/healthcare/provider/diagnostic-bookings/"


def make_centre():
    return ProviderFactory(provider_type="Lab", specialty="Diagnostics")


def make_booking(centre, **kwargs):
    booking = DiagnosticBooking.objects.create(
        patient=kwargs.pop("patient", None) or UserFactory(),
        center=centre,
        **kwargs,
    )
    booking.tests.add(DiagnosticTestFactory(sample_type="Urine"))
    return booking


# --------------------------------------------------------------------------- #
# The centre's queue
# --------------------------------------------------------------------------- #
def test_centre_sees_only_its_own_bookings(auth_client):
    centre = make_centre()
    mine = make_booking(centre)
    make_booking(make_centre())  # another centre's work

    resp = auth_client(centre.user).get(BOOKINGS_URL)

    assert resp.status_code == 200
    assert [row["id"] for row in resp.data] == [mine.id]


def test_non_provider_cannot_read_the_queue(auth_client):
    resp = auth_client(UserFactory()).get(BOOKINGS_URL)
    assert resp.status_code == 403


def test_booking_row_carries_the_patient_card_fields(auth_client):
    centre = make_centre()
    patient = UserFactory(
        name="Anjali Sharma",
        gender="female",
        dob=timezone.localdate() - timedelta(days=365 * 28 + 7),
    )
    make_booking(centre, patient=patient)

    row = auth_client(centre.user).get(BOOKINGS_URL).data[0]

    assert row["patient_name"] == "Anjali Sharma"
    assert row["patient_gender"] == "female"
    assert row["patient_age"] == 28
    assert row["tests"][0]["sample_type"] == "Urine"


def test_centre_accepts_a_booking(auth_client):
    centre = make_centre()
    booking = make_booking(centre)

    resp = auth_client(centre.user).patch(
        f"{BOOKINGS_URL}{booking.id}/",
        {"status": DiagnosticBookingStatus.CONFIRMED},
        format="json",
    )

    assert resp.status_code == 200
    booking.refresh_from_db()
    assert booking.status == DiagnosticBookingStatus.CONFIRMED


def test_centre_cannot_push_a_booking_back_to_requested(auth_client):
    """`requested` is where a booking starts. Nothing may move back into it —
    a centre could otherwise un-answer a request it had already accepted."""
    centre = make_centre()
    booking = make_booking(centre, status=DiagnosticBookingStatus.CONFIRMED)

    resp = auth_client(centre.user).patch(
        f"{BOOKINGS_URL}{booking.id}/",
        {"status": DiagnosticBookingStatus.REQUESTED},
        format="json",
    )

    assert resp.status_code == 400
    booking.refresh_from_db()
    assert booking.status == DiagnosticBookingStatus.CONFIRMED


def test_centre_cannot_touch_another_centres_booking(auth_client):
    centre = make_centre()
    other = make_booking(make_centre())

    resp = auth_client(centre.user).patch(
        f"{BOOKINGS_URL}{other.id}/",
        {"status": DiagnosticBookingStatus.CONFIRMED},
        format="json",
    )

    assert resp.status_code == 404


def test_uploaded_reports_append_rather_than_replace(auth_client):
    """A second upload is another test's result, not a correction of the first."""
    centre = make_centre()
    booking = make_booking(centre, status=DiagnosticBookingStatus.CONFIRMED)
    client = auth_client(centre.user)

    client.patch(f"{BOOKINGS_URL}{booking.id}/", {"reports": ["/media/a.pdf"]}, format="json")
    resp = client.patch(
        f"{BOOKINGS_URL}{booking.id}/",
        {"reports": ["/media/b.pdf"], "status": DiagnosticBookingStatus.REPORT_GENERATED},
        format="json",
    )

    assert resp.status_code == 200
    booking.refresh_from_db()
    assert booking.reports == ["/media/a.pdf", "/media/b.pdf"]
    assert booking.status == DiagnosticBookingStatus.REPORT_GENERATED


def test_reports_must_be_a_list_of_urls(auth_client):
    centre = make_centre()
    booking = make_booking(centre)

    resp = auth_client(centre.user).patch(
        f"{BOOKINGS_URL}{booking.id}/",
        {"reports": "/media/a.pdf"},
        format="json",
    )

    assert resp.status_code == 400
    booking.refresh_from_db()
    assert booking.reports == []


# --------------------------------------------------------------------------- #
# The doctor's appointment detail
# --------------------------------------------------------------------------- #
def test_appointment_detail_carries_the_payment_split_and_history(auth_client):
    profile = DoctorProfileFactory()
    patient = UserFactory(gender="female", dob=timezone.localdate() - timedelta(days=365 * 30 + 8))
    Appointment.objects.create(
        patient=patient,
        doctor=profile,
        scheduled_at=timezone.now() - timedelta(days=40),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.COMPLETED,
        fee=Decimal("700.00"),
    )
    current = Appointment.objects.create(
        patient=patient,
        doctor=profile,
        scheduled_at=timezone.now() + timedelta(hours=3),
        consultation_mode=ConsultationMode.IN_CLINIC,
        status=AppointmentStatus.CONFIRMED,
        fee=Decimal("700.00"),
        platform_fee=Decimal("11.00"),
    )

    resp = auth_client(profile.provider.user).get(
        f"/api/healthcare/provider/appointments/{current.id}/",
    )

    assert resp.status_code == 200
    assert resp.data["total_payable"] == "711.00"
    assert resp.data["patient_age"] == 30
    assert len(resp.data["previous_consultations"]) == 1


def test_appointment_list_leaves_out_the_history(auth_client):
    """It is one query per row for something no list renders."""
    profile = DoctorProfileFactory()
    patient = UserFactory()
    Appointment.objects.create(
        patient=patient,
        doctor=profile,
        scheduled_at=timezone.now() - timedelta(days=40),
        consultation_mode=ConsultationMode.ONLINE,
        status=AppointmentStatus.COMPLETED,
        fee=Decimal("700.00"),
    )
    Appointment.objects.create(
        patient=patient,
        doctor=profile,
        scheduled_at=timezone.now() + timedelta(hours=3),
        consultation_mode=ConsultationMode.IN_CLINIC,
        status=AppointmentStatus.CONFIRMED,
        fee=Decimal("700.00"),
    )

    rows = auth_client(profile.provider.user).get(
        "/api/healthcare/provider/appointments/",
    ).data

    assert all(row["previous_consultations"] == [] for row in rows)


def test_doctor_cannot_read_another_doctors_appointment(auth_client):
    mine = DoctorProfileFactory()
    theirs = DoctorProfileFactory()
    appointment = Appointment.objects.create(
        patient=UserFactory(),
        doctor=theirs,
        scheduled_at=timezone.now() + timedelta(hours=3),
        consultation_mode=ConsultationMode.ONLINE,
        fee=Decimal("500.00"),
    )

    resp = auth_client(mine.provider.user).get(
        f"/api/healthcare/provider/appointments/{appointment.id}/",
    )

    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# The auto-accept deadline
# --------------------------------------------------------------------------- #
def test_overdue_requests_are_accepted_when_the_queue_is_read(auth_client, settings):
    """Both apps promise the patient a deadline; reading the queue keeps it."""
    settings.AUTO_ACCEPT_MINUTES = 180
    centre = make_centre()
    overdue = make_booking(centre)
    fresh = make_booking(centre)

    # `created_at` is auto-set, so age the row rather than passing it in.
    DiagnosticBooking.objects.filter(pk=overdue.pk).update(
        created_at=timezone.now() - timedelta(minutes=200),
    )

    auth_client(centre.user).get(BOOKINGS_URL)

    overdue.refresh_from_db()
    fresh.refresh_from_db()
    assert overdue.status == DiagnosticBookingStatus.CONFIRMED
    assert fresh.status == DiagnosticBookingStatus.REQUESTED


def test_auto_accept_is_off_unless_a_window_is_configured(auth_client, settings):
    """Zero minutes means nothing is answered on the partner's behalf.

    This is the production default. Switching it on retroactively confirms
    every request already older than the window, so an overdue request must
    survive a queue read untouched while the setting is 0.
    """
    settings.AUTO_ACCEPT_MINUTES = 0
    centre = make_centre()
    overdue = make_booking(centre)
    DiagnosticBooking.objects.filter(pk=overdue.pk).update(
        created_at=timezone.now() - timedelta(days=7),
    )

    rows = auth_client(centre.user).get(BOOKINGS_URL).data

    overdue.refresh_from_db()
    assert overdue.status == DiagnosticBookingStatus.REQUESTED
    assert rows[0]["auto_accept_at"] is None


def test_pending_request_reports_when_it_will_be_accepted(auth_client, settings):
    settings.AUTO_ACCEPT_MINUTES = 180
    centre = make_centre()
    make_booking(centre)
    accepted = make_booking(centre, status=DiagnosticBookingStatus.CONFIRMED)

    rows = {row["id"]: row for row in auth_client(centre.user).get(BOOKINGS_URL).data}

    assert rows[accepted.id]["auto_accept_at"] is None
    pending = next(row for row in rows.values() if row["id"] != accepted.id)
    assert pending["auto_accept_at"] is not None
