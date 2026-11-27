"""A patient may only book hours the doctor actually offered.

None of this was checked before: any `scheduled_at` was accepted, including a
time outside every availability slot, a mode the doctor does not offer, a day
the doctor had blocked, and a slot another patient already had.
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import BlockedDate
from docatho_backend.healthcare.models import DoctorAvailability
from docatho_backend.healthcare.models import slot_is_taken
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _next_local(weekday_js: int, hour: int):
    """The next local datetime on that JS weekday (Sunday = 0) at that hour."""
    now = timezone.localtime(timezone.now())
    for ahead in range(1, 9):
        candidate = (now + timedelta(days=ahead)).replace(
            hour=hour, minute=0, second=0, microsecond=0,
        )
        if (candidate.weekday() + 1) % 7 == weekday_js:
            return candidate
    raise AssertionError("no matching weekday within a week")


def _book(client, doctor, when, mode="online"):
    return client.post(
        "/api/healthcare/appointments/",
        {
            "doctor": doctor.id,
            "scheduled_at": when.isoformat(),
            "consultation_mode": mode,
            "symptoms": "Fever",
            "payment_method": "online",
        },
        format="json",
    )


@pytest.fixture
def doctor_with_monday_hours():
    doctor = DoctorProfileFactory(is_verified=True, consultation_modes=["online"])
    DoctorAvailability.objects.create(
        doctor=doctor,
        day_of_week=1,  # Monday, in the JS getDay() numbering the apps send
        start_time="09:00",
        end_time="17:00",
        consultation_mode="online",
        is_active=True,
    )
    return doctor


@pytest.fixture
def patient_client(api_client):
    client = api_client
    client.force_authenticate(UserFactory())
    return client


def test_books_inside_the_window(doctor_with_monday_hours, patient_client):
    response = _book(patient_client, doctor_with_monday_hours, _next_local(1, 10))
    assert response.status_code == 201, response.data


def test_rejects_a_time_outside_every_slot(doctor_with_monday_hours, patient_client):
    response = _book(patient_client, doctor_with_monday_hours, _next_local(1, 3))
    assert response.status_code == 400
    assert "scheduled_at" in response.data


def test_rejects_a_day_with_no_hours(doctor_with_monday_hours, patient_client):
    response = _book(patient_client, doctor_with_monday_hours, _next_local(0, 10))
    assert response.status_code == 400


def test_rejects_a_mode_the_doctor_does_not_offer(
    doctor_with_monday_hours,
    patient_client,
):
    response = _book(
        patient_client,
        doctor_with_monday_hours,
        _next_local(1, 10),
        mode="home_visit",
    )
    assert response.status_code == 400
    assert "consultation_mode" in response.data


def test_rejects_a_blocked_date(doctor_with_monday_hours, patient_client):
    when = _next_local(1, 10)
    BlockedDate.objects.create(
        doctor=doctor_with_monday_hours,
        date=when.date(),
        reason="Leave",
    )
    response = _book(patient_client, doctor_with_monday_hours, when)
    assert response.status_code == 400


def test_rejects_a_slot_already_taken(doctor_with_monday_hours, patient_client):
    when = _next_local(1, 10)
    assert _book(patient_client, doctor_with_monday_hours, when).status_code == 201
    second = _book(patient_client, doctor_with_monday_hours, when)
    assert second.status_code == 400


def test_a_cancelled_booking_frees_the_slot(doctor_with_monday_hours, patient_client):
    when = _next_local(1, 10)
    first = _book(patient_client, doctor_with_monday_hours, when)
    Appointment.objects.filter(pk=first.data["id"]).update(
        status=AppointmentStatus.CANCELLED,
    )
    assert _book(patient_client, doctor_with_monday_hours, when).status_code == 201


def test_rejects_a_time_in_the_past(doctor_with_monday_hours, patient_client):
    past = timezone.localtime(timezone.now()) - timedelta(days=7)
    response = _book(patient_client, doctor_with_monday_hours, past)
    assert response.status_code == 400


def test_a_doctor_with_no_hours_stays_bookable(patient_client):
    """The 47 doctors in production who never opened the availability screen."""
    doctor = DoctorProfileFactory(is_verified=True, consultation_modes=["online"])
    response = _book(patient_client, doctor, _next_local(3, 4))
    assert response.status_code == 201, response.data


def test_the_database_refuses_a_second_live_booking_of_one_slot(
    doctor_with_monday_hours,
):
    """The serializer's check can lose a race; the constraint cannot.

    Two requests can both read a free slot before either writes, which is how
    two patients ended up with the same doctor at the same minute.
    """
    when = _next_local(1, 10)

    def book(status):
        return Appointment.objects.create(
            patient=UserFactory(),
            doctor=doctor_with_monday_hours,
            scheduled_at=when,
            consultation_mode="online",
            status=status,
            fee=500,
        )

    book(AppointmentStatus.CONFIRMED)
    with pytest.raises(IntegrityError), transaction.atomic():
        book(AppointmentStatus.PENDING)


def test_a_rejected_booking_does_not_block_the_slot(doctor_with_monday_hours):
    """The constraint is conditional — a dead booking must not hold a slot."""
    when = _next_local(1, 10)
    for status in (AppointmentStatus.REJECTED, AppointmentStatus.NO_SHOW):
        Appointment.objects.create(
            patient=UserFactory(),
            doctor=doctor_with_monday_hours,
            scheduled_at=when,
            consultation_mode="online",
            status=status,
            fee=500,
        )
    assert not slot_is_taken(doctor_with_monday_hours, when)


def test_doctor_detail_names_the_slots_already_gone(
    doctor_with_monday_hours,
    patient_client,
):
    """The booking screen cuts `availability` into slots, so it needs the
    taken ones — it used to offer a slot and 400 at the end of the flow."""
    when = _next_local(1, 10)
    assert _book(patient_client, doctor_with_monday_hours, when).status_code == 201

    detail = patient_client.get(
        f"/api/healthcare/doctors/{doctor_with_monday_hours.provider_id}/",
    )

    assert detail.status_code == 200
    taken = Appointment.objects.get().scheduled_at.isoformat()
    assert detail.data["booked_slots"] == [taken]
