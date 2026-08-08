"""Seed healthcare demo data: specialties, doctors, diagnostics, content pages.

Usage: uv run python manage.py seed_healthcare
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from docatho_backend.healthcare.models import ContentPage
from docatho_backend.healthcare.models import ContentPageType
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import DiagnosticTest
from docatho_backend.healthcare.models import DiagnosticTestCategory
from docatho_backend.healthcare.models import DoctorAvailability
from docatho_backend.healthcare.models import DoctorProfile
from docatho_backend.healthcare.models import MedicalSpecialty
from docatho_backend.healthcare.models import VerificationStatus
from docatho_backend.providers.enums import ProviderType
from docatho_backend.providers.models import Provider
from docatho_backend.users.models import User

SPECIALTIES = [
    "General Physician",
    "Cardiologist",
    "Dermatologist",
    "Pediatrician",
    "Orthopedic",
    "Gynecologist",
]

DIAGNOSTIC_CATEGORIES = [
    "Blood Tests",
    "Imaging",
    "Hormone Tests",
    "Allergy Tests",
]

TESTS = [
    ("Complete Blood Count", "Blood Tests", "450.00", "No special preparation."),
    ("Lipid Profile", "Blood Tests", "650.00", "12-hour fasting recommended."),
    ("Thyroid Profile", "Hormone Tests", "550.00", "No fasting required."),
    ("Chest X-Ray", "Imaging", "400.00", "Remove metal objects."),
    ("HbA1c", "Blood Tests", "500.00", "No fasting required."),
    ("Vitamin D", "Blood Tests", "900.00", "No special preparation."),
]

DOCTORS = [
    ("Dr. Ananya Sharma", "Cardiologist", "Mumbai", "12", "800.00", "1200.00"),
    ("Dr. Rohit Mehta", "General Physician", "Mumbai", "8", "400.00", "600.00"),
    ("Dr. Priya Nair", "Dermatologist", "Pune", "10", "500.00", "800.00"),
]

CONTENT_PAGES = [
    (ContentPageType.FAQ, "How do I book a doctor?", "Browse doctors, pick a slot, and confirm."),
    (ContentPageType.ABOUT, "About Docatho", "Docatho connects patients with doctors, labs, and pharmacies."),
    (ContentPageType.PRIVACY, "Privacy Policy", "We protect your health data per applicable regulations."),
    (ContentPageType.TERMS, "Terms & Conditions", "By using Docatho you agree to our service terms."),
]


class Command(BaseCommand):
    help = "Seed healthcare specialties, doctors, diagnostic tests, and content pages."

    @transaction.atomic
    def handle(self, *args, **options):
        specialty_map = {}
        for name in SPECIALTIES:
            obj, _ = MedicalSpecialty.objects.get_or_create(name=name, defaults={"is_active": True})
            specialty_map[name] = obj

        cat_map = {}
        for name in DIAGNOSTIC_CATEGORIES:
            obj, _ = DiagnosticTestCategory.objects.get_or_create(name=name, defaults={"is_active": True})
            cat_map[name] = obj

        for name, cat, price, prep in TESTS:
            DiagnosticTest.objects.get_or_create(
                name=name,
                defaults={
                    "category": cat_map[cat],
                    "price": Decimal(price),
                    "preparation_instructions": prep,
                    "is_active": True,
                },
            )

        for page_type, title, body in CONTENT_PAGES:
            ContentPage.objects.get_or_create(
                page_type=page_type,
                title=title,
                defaults={"body": body, "is_published": True},
            )

        for idx, (name, specialty, city, exp, fee_online, fee_clinic) in enumerate(DOCTORS):
            phone = f"+9199000{idx:04d}"
            user, _ = User.objects.get_or_create(
                phone=phone,
                defaults={"name": name, "email": f"doctor{idx}@docatho.test"},
            )
            provider, _ = Provider.objects.get_or_create(
                user=user,
                defaults={
                    "name": name,
                    "specialty": specialty,
                    "provider_type": ProviderType.DOCTOR.value,
                },
            )
            profile, _ = DoctorProfile.objects.get_or_create(
                provider=provider,
                defaults={
                    "biography": f"{name} provides quality care in {specialty}.",
                    "experience_years": int(exp),
                    "fee_online": Decimal(fee_online),
                    "fee_in_clinic": Decimal(fee_clinic),
                    "consultation_modes": [
                        ConsultationMode.ONLINE,
                        ConsultationMode.IN_CLINIC,
                    ],
                    "clinic_city": city,
                    "clinic_name": f"{name} Clinic",
                    "verification_status": VerificationStatus.APPROVED,
                    "is_verified": True,
                    "rating_avg": Decimal("4.50"),
                    "review_count": 25,
                },
            )
            if specialty in specialty_map:
                profile.specialties.add(specialty_map[specialty])
            for dow in range(1, 6):
                DoctorAvailability.objects.get_or_create(
                    doctor=profile,
                    day_of_week=dow,
                    start_time="09:00",
                    consultation_mode=ConsultationMode.ONLINE,
                    defaults={"end_time": "17:00", "is_active": True},
                )

        self.stdout.write(self.style.SUCCESS("Healthcare seed data created."))
