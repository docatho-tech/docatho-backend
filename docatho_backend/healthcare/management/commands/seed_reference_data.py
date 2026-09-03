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

QUALIFICATIONS = [
    "MBBS", "MD", "MS", "DM", "MCh", "DNB", "DGO", "DCH", "DO", "DLO",
    "DPM", "DA", "DVD", "MDS", "BDS", "BAMS", "BHMS", "BUMS", "BPT", "MPT",
    "BSc Nursing", "MSc Nursing", "PhD", "FRCS", "MRCP", "FRCP", "FICS",
    "FACS", "Diploma in Diabetology", "Fellowship in Cardiology",
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
            for name in names:
                _, was_created = model.objects.get_or_create(name=name)
                created += int(was_created)
            total = model.objects.count()
            self.stdout.write(
                self.style.SUCCESS(f"{label}: {created} added, {total} total"),
            )
