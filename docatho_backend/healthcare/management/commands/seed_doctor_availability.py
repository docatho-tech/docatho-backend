"""Give doctors a standard weekly timetable for clinic and online consultations.

Doctors imported in bulk arrive with no `DoctorAvailability` rows at all, which
means the patient app's booking screen has nothing to turn into slots and every
day tab reads "0 Slots". This backfills one standard timetable so an imported
doctor is bookable, and leaves alone anyone who has already published their own
hours.

    uv run python manage.py seed_doctor_availability              # dry run
    uv run python manage.py seed_doctor_availability --apply
    uv run python manage.py seed_doctor_availability --apply --doctor 389

`day_of_week` is JavaScript's `Date.getDay()` — **0 = Sunday**, 6 = Saturday.
That is not Python's `weekday()`. The convention is set by the client, which
matches windows with `w.day_of_week === day.getDay()`
(`customer-app/src/utils/doctorSlots.ts`), and nothing on the server reads the
field, so the client is the only definition there is.

The timetable below is deliberately non-overlapping: a doctor cannot be in the
clinic and on a video call at the same time, and the two modes are rendered from
the same weekly windows.

    Mon-Sat   08:00-10:00  online      (before clinic)
    Mon-Sat   10:00-13:00  in-clinic
    Mon-Sat   17:00-20:00  in-clinic
    Mon-Sat   20:00-22:00  online      (after clinic)
    Sunday    10:00-13:00  online      (no clinic on Sunday)

The client cuts each window into 30-minute slots, so that is 12 clinic slots and
8 online slots on a weekday, and 6 online slots on Sunday.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import DoctorAvailability
from docatho_backend.healthcare.models import DoctorProfile

MONDAY_TO_SATURDAY = (1, 2, 3, 4, 5, 6)
SUNDAY = 0

# (start, end, mode) applied to every day in the second element.
STANDARD_WEEK: list[tuple[str, str, str, tuple[int, ...]]] = [
    ("08:00", "10:00", ConsultationMode.ONLINE, MONDAY_TO_SATURDAY),
    ("10:00", "13:00", ConsultationMode.IN_CLINIC, MONDAY_TO_SATURDAY),
    ("17:00", "20:00", ConsultationMode.IN_CLINIC, MONDAY_TO_SATURDAY),
    ("20:00", "22:00", ConsultationMode.ONLINE, MONDAY_TO_SATURDAY),
    ("10:00", "13:00", ConsultationMode.ONLINE, (SUNDAY,)),
]


def standard_rows() -> list[tuple[int, str, str, str]]:
    """(day_of_week, start, end, mode) for one doctor's whole week."""
    return [
        (day, start, end, mode)
        for start, end, mode, days in STANDARD_WEEK
        for day in days
    ]


class Command(BaseCommand):
    help = "Backfill a standard weekly timetable for doctors with no published hours."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the rows. Without this the command only reports what it would do.",
        )
        parser.add_argument(
            "--doctor",
            type=int,
            action="append",
            dest="doctors",
            help="Limit to this DoctorProfile id. Repeatable.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help=(
                "Also give the standard week to doctors who already publish hours "
                "for that mode. Off by default: their own hours are real data."
            ),
        )
        parser.add_argument(
            "--no-modes",
            action="store_true",
            help=(
                "Do not touch consultation_modes. By default an EMPTY "
                "consultation_modes is filled in, because the booking screen hides "
                "a mode the doctor does not list and the new hours would be invisible."
            ),
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        overwrite = options["overwrite"]
        rows = standard_rows()

        doctors = DoctorProfile.objects.select_related("provider").order_by("pk")
        if options.get("doctors"):
            doctors = doctors.filter(pk__in=options["doctors"])

        created = skipped_existing = 0
        touched_doctors: list[str] = []
        modes_filled: list[str] = []
        zero_fee_online: list[str] = []

        with transaction.atomic():
            for doctor in doctors:
                label = f"[{doctor.pk}] {doctor.provider.name}"
                published = set(
                    doctor.availability_slots.filter(is_active=True)
                    .values_list("consultation_mode", flat=True),
                )

                made_here = 0
                for day, start, end, mode in rows:
                    if mode in published and not overwrite:
                        skipped_existing += 1
                        continue
                    if not apply:
                        exists = doctor.availability_slots.filter(
                            day_of_week=day,
                            start_time=start,
                            consultation_mode=mode,
                        ).exists()
                        if not exists:
                            made_here += 1
                        continue
                    _, was_created = DoctorAvailability.objects.get_or_create(
                        doctor=doctor,
                        day_of_week=day,
                        start_time=start,
                        consultation_mode=mode,
                        defaults={"end_time": end, "is_active": True},
                    )
                    if was_created:
                        made_here += 1

                if made_here:
                    created += made_here
                    touched_doctors.append(f"{label} (+{made_here})")

                # Hours the doctor does not "offer" are not rendered at all.
                if not options["no_modes"] and not doctor.consultation_modes and made_here:
                    modes_filled.append(label)
                    if apply:
                        doctor.consultation_modes = [
                            ConsultationMode.IN_CLINIC,
                            ConsultationMode.ONLINE,
                        ]
                        doctor.save(update_fields=["consultation_modes", "updated_at"])

                # Reported, never guessed: `Appointment.fee` is copied from these
                # and a zero fee is a zero-rupee Razorpay order, which Razorpay
                # rejects — so an online booking cannot be paid for at all.
                if made_here and not doctor.fee_online:
                    zero_fee_online.append(label)

            if not apply:
                transaction.set_rollback(True)

        verb = "Created" if apply else "Would create"
        self.stdout.write(f"{verb} {created} availability row(s) across {len(touched_doctors)} doctor(s).")
        self.stdout.write(f"Left {skipped_existing} row(s) alone — doctor already publishes that mode.")
        if modes_filled:
            word = "Filled" if apply else "Would fill"
            self.stdout.write(f"{word} empty consultation_modes on {len(modes_filled)} doctor(s).")
        if zero_fee_online:
            self.stdout.write(
                self.style.WARNING(
                    f"\n{len(zero_fee_online)} doctor(s) now publish online hours but have "
                    f"fee_online = 0. Razorpay rejects a zero-rupee order, so those "
                    f"consultations cannot be paid for and the booking will fail at checkout:",
                ),
            )
            for label in zero_fee_online[:10]:
                self.stdout.write(f"  - {label}")
            if len(zero_fee_online) > 10:
                self.stdout.write(f"  … and {len(zero_fee_online) - 10} more")
        if not apply:
            self.stdout.write(self.style.NOTICE("\nDry run — nothing was written. Re-run with --apply."))
