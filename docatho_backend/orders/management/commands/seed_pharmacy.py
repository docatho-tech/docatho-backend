"""Seed realistic e-pharmacy demo data across all surfaces.

Usage:  uv run python manage.py seed_pharmacy [--flush]

Creates categories, a mixed OTC/Rx catalogue, a demo customer, two chemist
providers, addresses, and one order in each fulfilment status (mixed COD /
online-paid, with commission computed and stock reserved where appropriate).

Idempotent: safe to re-run; existing rows are reused via get_or_create.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from docatho_backend.medicines.models import Category
from docatho_backend.medicines.models import DrugSchedule
from docatho_backend.medicines.models import Medicine
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import OrderItem
from docatho_backend.providers.models import Provider
from docatho_backend.users.models import Address
from docatho_backend.users.models import User

CATEGORIES = ["Pain Relief", "Antibiotics", "Vitamins", "Skin Care", "Cardiac Care"]

# (name, brand, manufacturer, price, mrp, stock, schedule, category)
MEDICINES = [
    ("Paracetamol 500mg", "Calpol", "GSK", "25.00", "30.00", 200, DrugSchedule.OTC, "Pain Relief"),
    ("Ibuprofen 400mg", "Brufen", "Abbott", "35.00", "42.00", 150, DrugSchedule.OTC, "Pain Relief"),
    ("Amoxicillin 500mg", "Mox", "Sun Pharma", "85.00", "99.00", 80, DrugSchedule.H, "Antibiotics"),
    ("Azithromycin 500mg", "Azithral", "Alembic", "120.00", "140.00", 60, DrugSchedule.H, "Antibiotics"),
    ("Alprazolam 0.5mg", "Alprax", "Torrent", "45.00", "55.00", 40, DrugSchedule.H1, "Pain Relief"),
    ("Vitamin C 1000mg", "Limcee", "Abbott", "18.00", "22.00", 300, DrugSchedule.OTC, "Vitamins"),
    ("Vitamin D3 60k", "Uprise D3", "Alkem", "55.00", "65.00", 120, DrugSchedule.OTC, "Vitamins"),
    ("Cetirizine 10mg", "Cetzine", "Dr Reddy", "20.00", "25.00", 180, DrugSchedule.OTC, "Skin Care"),
    ("Atorvastatin 10mg", "Atorva", "Zydus", "95.00", "110.00", 90, DrugSchedule.H, "Cardiac Care"),
    ("Morphine 10mg", "Morcontin", "Modi-Mundipharma", "180.00", "210.00", 15, DrugSchedule.X, "Pain Relief"),
]

# label -> (status, payment_method, payment_status)
ORDER_SCENARIOS = [
    ("placed", Order.Status.PLACED, Order.PaymentMethod.ONLINE, Order.PaymentStatus.PENDING),
    ("approved", Order.Status.APPROVED, Order.PaymentMethod.ONLINE, Order.PaymentStatus.PAID),
    ("packed", Order.Status.PACKED, Order.PaymentMethod.COD, Order.PaymentStatus.PENDING),
    ("out_for_delivery", Order.Status.OUT_FOR_DELIVERY, Order.PaymentMethod.ONLINE, Order.PaymentStatus.PAID),
    ("delivered", Order.Status.DELIVERED, Order.PaymentMethod.COD, Order.PaymentStatus.PAID),
    ("rejected", Order.Status.REJECTED, Order.PaymentMethod.ONLINE, Order.PaymentStatus.REFUNDED),
]


class Command(BaseCommand):
    help = "Seed realistic e-pharmacy demo data (catalogue, providers, orders)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing demo orders before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            Order.objects.filter(order_number__startswith="ORDSEED").delete()
            self.stdout.write("Flushed existing seed orders.")

        categories = {
            name: Category.objects.get_or_create(name=name)[0] for name in CATEGORIES
        }
        medicines = {}
        for (name, brand, mfr, price, mrp, stock, schedule, cat) in MEDICINES:
            med, _ = Medicine.objects.get_or_create(
                name=name,
                defaults={
                    "brand": brand,
                    "manufacturer": mfr,
                    "price": Decimal(price),
                    "mrp": Decimal(mrp),
                    "stock": stock,
                    "schedule": schedule,
                },
            )
            med.category.add(categories[cat])
            medicines[name] = med

        customer = self._user("+919800000001", "Demo Patient", "patient@docatho.test")
        Address.objects.get_or_create(
            user=customer,
            address_line1="42 MG Road",
            defaults={
                "city": "Bengaluru",
                "state": "Karnataka",
                "postal_code": "560001",
                "country": "India",
                "is_default": True,
            },
        )

        providers = []
        for i, (phone, pname) in enumerate(
            [("+919811000001", "HealthPlus Chemist"), ("+919811000002", "CityMed Pharmacy")],
        ):
            puser = self._user(phone, pname + " Owner", f"provider{i}@docatho.test")
            provider, _ = Provider.objects.get_or_create(
                user=puser,
                defaults={
                    "name": pname,
                    "specialty": "Pharmacy",
                    "provider_type": "Chemist",
                    "bank_account_number": f"00012345{i}",
                    "bank_ifsc": "HDFC0001234",
                },
            )
            providers.append(provider)

        seed_meds = [medicines["Paracetamol 500mg"], medicines["Vitamin C 1000mg"]]
        created = 0
        for idx, (_label, status, pmethod, pstatus) in enumerate(ORDER_SCENARIOS):
            number = f"ORDSEED{idx:04d}"
            if Order.objects.filter(order_number=number).exists():
                continue
            order = Order.objects.create(
                order_number=number,
                user=customer,
                address=customer.address,
                assigned_provider=providers[idx % len(providers)],
                payment_method=pmethod,
                payment_status=pstatus,
                status=status,
                placed_at=timezone.now(),
            )
            for med in seed_meds:
                OrderItem.objects.create(
                    order=order, medicine=med, quantity=1 + (idx % 2),
                )
            order.recalc_totals()
            order.compute_commission()
            if status == Order.Status.DELIVERED:
                order.delivered_at = timezone.now()
                order.save(update_fields=["delivered_at"])
            created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(categories)} categories, {len(medicines)} medicines, "
                f"{len(providers)} providers, {created} orders.",
            ),
        )

    def _user(self, phone: str, name: str, email: str) -> User:
        user = User.objects.filter(phone=phone).first()
        if user:
            return user
        return User.objects.create_user(phone=phone, name=name, email=email)
