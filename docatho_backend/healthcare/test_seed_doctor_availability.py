"""`seed_doctor_availability` writes the standard week without clobbering real hours.

The command exists to be run against production, where 41 of 47 doctors have no
published hours. The two things that must hold there are that a second run is a
no-op, and that a doctor who already publishes their own hours for a mode keeps
them.
"""

import pytest
from django.core.management import call_command

from docatho_backend.healthcare.management.commands.seed_doctor_availability import standard_rows
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import DoctorAvailability
from docatho_backend.healthcare.models import DoctorProfile
from docatho_backend.healthcare.models import VerificationStatus
from docatho_backend.providers.enums import ProviderType
from docatho_backend.providers.models import Provider
from docatho_backend.users.models import User

ROWS_PER_DOCTOR = 25  # 6 days x 4 windows + Sunday online


def make_doctor(phone: str, name: str, **kwargs) -> DoctorProfile:
    user = User.objects.create_user(phone=phone, password="pw")
    provider = Provider.objects.create(
        name=name,
        user=user,
        provider_type=ProviderType.DOCTOR.value,
    )
    return DoctorProfile.objects.create(
        provider=provider,
        is_verified=True,
        verification_status=VerificationStatus.APPROVED,
        **kwargs,
    )


@pytest.fixture
def bare(db) -> DoctorProfile:
    """The production shape: no hours, no modes, no fee."""
    return make_doctor("+919000000041", "Dr. Bare")


def test_standard_week_is_whole_and_unique():
    rows = standard_rows()
    assert len(rows) == ROWS_PER_DOCTOR
    # unique_together is (doctor, day, start_time, mode) — a duplicate here
    # would be an IntegrityError on the second doctor, not a visible bug.
    assert len({(d, s, m) for d, s, _, m in rows}) == len(rows)
    assert {m for *_, m in rows} == {ConsultationMode.ONLINE, ConsultationMode.IN_CLINIC}


def test_dry_run_writes_nothing(bare):
    call_command("seed_doctor_availability")
    assert DoctorAvailability.objects.count() == 0
    bare.refresh_from_db()
    assert bare.consultation_modes == []


def test_apply_is_idempotent_and_fills_modes(bare):
    call_command("seed_doctor_availability", "--apply")
    assert DoctorAvailability.objects.filter(doctor=bare).count() == ROWS_PER_DOCTOR
    bare.refresh_from_db()
    assert set(bare.consultation_modes) == {
        ConsultationMode.IN_CLINIC,
        ConsultationMode.ONLINE,
    }

    call_command("seed_doctor_availability", "--apply")
    assert DoctorAvailability.objects.filter(doctor=bare).count() == ROWS_PER_DOCTOR


def test_a_doctor_who_publishes_hours_keeps_them(db):
    doctor = make_doctor("+919000000042", "Dr. Busy", consultation_modes=["online"])
    DoctorAvailability.objects.create(
        doctor=doctor,
        day_of_week=1,
        start_time="06:00",
        end_time="07:00",
        consultation_mode=ConsultationMode.ONLINE,
    )

    call_command("seed_doctor_availability", "--apply")

    online = doctor.availability_slots.filter(consultation_mode=ConsultationMode.ONLINE)
    assert online.count() == 1, "their own online hours were replaced"
    # The mode they do not publish is still filled in, which is the point.
    assert doctor.availability_slots.filter(
        consultation_mode=ConsultationMode.IN_CLINIC,
    ).exists()
    doctor.refresh_from_db()
    assert doctor.consultation_modes == ["online"], "a declared mode list was rewritten"
