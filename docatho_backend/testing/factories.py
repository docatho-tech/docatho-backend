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
