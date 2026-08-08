"""Shared factory-boy factories for the e-pharmacy test suite."""

from decimal import Decimal

import factory
from factory.django import DjangoModelFactory

from docatho_backend.medicines.models import Category
from docatho_backend.medicines.models import DrugSchedule
from docatho_backend.medicines.models import Medicine
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import OrderItem
from docatho_backend.orders.models import Prescription
from docatho_backend.providers.models import Provider
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import DiagnosticTest
from docatho_backend.healthcare.models import DiagnosticTestCategory
from docatho_backend.healthcare.models import DoctorProfile
from docatho_backend.healthcare.models import MedicalSpecialty
from docatho_backend.healthcare.models import VerificationStatus
from docatho_backend.providers.enums import ProviderType

from docatho_backend.users.models import Address
from docatho_backend.users.models import User


class UserFactory(DjangoModelFactory):
    name = factory.Faker("name")
    email = factory.Sequence(lambda n: f"user{n}@example.com")
    phone = factory.Sequence(lambda n: f"+9198{n:08d}")

    class Meta:
        model = User


class AdminUserFactory(UserFactory):
    is_staff = True
    is_superuser = True


class ProviderFactory(DjangoModelFactory):
    name = factory.Faker("company")
    specialty = "Pharmacy"
    provider_type = "Chemist"
    user = factory.SubFactory(UserFactory)

    class Meta:
        model = Provider


class AddressFactory(DjangoModelFactory):
    user = factory.SubFactory(UserFactory)
    address_line1 = factory.Faker("street_address")
    city = "Mumbai"
    state = "Maharashtra"
    postal_code = "400001"
    country = "India"

    class Meta:
        model = Address


class CategoryFactory(DjangoModelFactory):
    name = factory.Sequence(lambda n: f"Category {n}")
    is_active = True

    class Meta:
        model = Category


class MedicineFactory(DjangoModelFactory):
    name = factory.Sequence(lambda n: f"Medicine {n}")
    brand = factory.Faker("word")
    manufacturer = factory.Faker("company")
    price = Decimal("100.00")
    mrp = Decimal("120.00")
    stock = 50
    schedule = DrugSchedule.OTC
    is_prescription_required = False
    is_active = True

    class Meta:
        model = Medicine
        skip_postgeneration_save = True

    @factory.post_generation
    def categories(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for category in extracted:
                self.category.add(category)


class RxMedicineFactory(MedicineFactory):
    """A prescription-required (Schedule H) medicine."""

    schedule = DrugSchedule.H
    is_prescription_required = True


class PrescriptionFactory(DjangoModelFactory):
    user = factory.SubFactory(UserFactory)
    image = factory.django.FileField(filename="rx.pdf", data=b"%PDF-1.4 test")

    class Meta:
        model = Prescription


class OrderFactory(DjangoModelFactory):
    order_number = factory.Sequence(lambda n: f"ORDTEST{n:06d}")
    user = factory.SubFactory(UserFactory)

    class Meta:
        model = Order


class OrderItemFactory(DjangoModelFactory):
    order = factory.SubFactory(OrderFactory)
    medicine = factory.SubFactory(MedicineFactory)
    quantity = 1
    unit_price = Decimal("100.00")
    mrp = Decimal("120.00")

    class Meta:
        model = OrderItem


class MedicalSpecialtyFactory(DjangoModelFactory):
    name = factory.Sequence(lambda n: f"Specialty {n}")
    is_active = True

    class Meta:
        model = MedicalSpecialty


class DoctorProviderFactory(ProviderFactory):
    provider_type = ProviderType.DOCTOR.value
    specialty = "General Physician"


class DoctorProfileFactory(DjangoModelFactory):
    provider = factory.SubFactory(DoctorProviderFactory)
    biography = factory.Faker("paragraph")
    experience_years = 5
    fee_online = Decimal("500.00")
    fee_in_clinic = Decimal("700.00")
    fee_home_visit = Decimal("900.00")
    consultation_modes = [ConsultationMode.ONLINE, ConsultationMode.IN_CLINIC]
    clinic_city = "Mumbai"
    verification_status = VerificationStatus.APPROVED
    is_verified = True
    rating_avg = Decimal("4.50")
    review_count = 10

    class Meta:
        model = DoctorProfile
        skip_postgeneration_save = True

    @factory.post_generation
    def specialties(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for sp in extracted:
                self.specialties.add(sp)


class DiagnosticTestCategoryFactory(DjangoModelFactory):
    name = factory.Sequence(lambda n: f"Diag Category {n}")
    is_active = True

    class Meta:
        model = DiagnosticTestCategory


class DiagnosticTestFactory(DjangoModelFactory):
    name = factory.Sequence(lambda n: f"Lab Test {n}")
    category = factory.SubFactory(DiagnosticTestCategoryFactory)
    price = Decimal("499.00")
    is_active = True

    class Meta:
        model = DiagnosticTest
