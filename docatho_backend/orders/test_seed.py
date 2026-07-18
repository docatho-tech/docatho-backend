"""The seed_pharmacy management command produces coherent demo data."""

import pytest
from django.core.management import call_command

from docatho_backend.medicines.models import Medicine
from docatho_backend.orders.models import Order
from docatho_backend.providers.models import Provider

pytestmark = pytest.mark.django_db


def test_seed_pharmacy_creates_data():
    call_command("seed_pharmacy")
    assert Medicine.objects.count() >= 10
    assert Provider.objects.count() >= 2
    # at least one order in each seeded status
    statuses = set(Order.objects.values_list("status", flat=True))
    assert {"placed", "approved", "packed", "out_for_delivery", "delivered"} <= statuses
    # Rx medicines were flagged
    assert Medicine.objects.filter(is_prescription_required=True).exists()
    # commission was computed on seeded orders
    assert Order.objects.filter(commission_amount__gt=0).exists()


def test_seed_is_idempotent():
    call_command("seed_pharmacy")
    orders_first = Order.objects.count()
    meds_first = Medicine.objects.count()
    call_command("seed_pharmacy")
    assert Order.objects.count() == orders_first
    assert Medicine.objects.count() == meds_first
