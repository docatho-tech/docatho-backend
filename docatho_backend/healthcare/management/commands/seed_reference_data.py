"""Seed the pick-lists the admin forms offer: specialties, test categories, degrees.

These are reference data, not demo data — `seed_healthcare` creates sample
doctors and bookings, which you do not want on a live database. This command
creates only the option rows the dashboard's pickers read, so it is safe to run
against production.

    python manage.py seed_reference_data

Idempotent, and additive only: an existing row is left exactly as it is, so a
name an admin has edited or an icon they have uploaded is never overwritten.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from docatho_backend.healthcare.models import DiagnosticTestCategory
from docatho_backend.healthcare.models import MedicalSpecialty
from docatho_backend.healthcare.models import Qualification

SPECIALTIES = [
    "General Physician", "Cardiologist", "Dermatologist", "Pediatrician",
    "Orthopedic", "Gynecologist", "Neurologist", "Psychiatrist",
    "ENT Specialist", "Ophthalmologist", "Dentist", "Gastroenterologist",
    "Pulmonologist", "Endocrinologist", "Nephrologist", "Urologist",
    "Oncologist", "Rheumatologist", "General Surgeon", "Physiotherapist",
    "Dietitian / Nutritionist", "Psychologist", "Ayurveda", "Homeopathy",
]

DIAGNOSTIC_CATEGORIES = [
    "Blood Tests", "Imaging & Radiology", "Hormone Tests", "Allergy Tests",
    "Diabetes Screening", "Heart & Cardiac", "Liver Function",
    "Kidney Function", "Thyroid Profile", "Vitamin & Mineral",
    "Infection & Fever", "Women's Health", "Full Body Checkup",
    "Urine & Stool",
]

#: Degrees, with the level each one belongs to.
#:
#: The level is not decoration: the doctor form has separate undergraduate and
#: postgraduate pickers, and seeding every degree as "other" left both of them
#: permanently empty — a picker that could never gain an option, which is the
#: dead end the catalogue managers exist to prevent.
QUALIFICATIONS = [
    ("MBBS", "ug"),
    ("BDS", "ug"),
    ("BAMS", "ug"),
    ("BHMS", "ug"),
    ("BUMS", "ug"),
    ("BPT", "ug"),
    ("BSc Nursing", "ug"),
    ("MD", "pg"),
    ("MS", "pg"),
    ("DM", "pg"),
    ("MCh", "pg"),
    ("DNB", "pg"),
    ("DGO", "pg"),
    ("DCH", "pg"),
    ("DO", "pg"),
    ("DLO", "pg"),
    ("DPM", "pg"),
    ("DA", "pg"),
    ("DVD", "pg"),
    ("MDS", "pg"),
    ("MPT", "pg"),
    ("MSc Nursing", "pg"),
    ("PhD", "pg"),
    ("FRCS", "other"),
    ("MRCP", "other"),
    ("FRCP", "other"),
    ("FICS", "other"),
    ("FACS", "other"),
    ("Diploma in Diabetology", "other"),
    ("Fellowship in Cardiology", "other"),
]


class Command(BaseCommand):
    help = "Create the specialty, test-category and qualification pick-lists."

    @transaction.atomic
    def handle(self, *args, **options):
        for model, names, label in (
            (MedicalSpecialty, SPECIALTIES, "specialties"),
            (DiagnosticTestCategory, DIAGNOSTIC_CATEGORIES, "test categories"),
            (Qualification, QUALIFICATIONS, "qualifications"),
        ):
            created = 0
            for entry in names:
                name, level = entry if isinstance(entry, tuple) else (entry, None)
                obj, was_created = model.objects.get_or_create(name=name)
                # Backfill on re-run: an install seeded before levels existed
                # has every degree at "other", and both pickers stay empty
                # until something corrects them.
                if level and getattr(obj, "level", None) in (None, "", "other"):
                    obj.level = level
                    obj.save(update_fields=["level"])
                created += int(was_created)
            total = model.objects.count()
            self.stdout.write(
                self.style.SUCCESS(f"{label}: {created} added, {total} total"),
            )
