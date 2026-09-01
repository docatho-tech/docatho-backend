from decimal import Decimal

from docatho_backend.medicines.management.commands.import_medicines_csv import (
    description_of,
)
from docatho_backend.medicines.management.commands.import_medicines_csv import (
    display_name,
)
from docatho_backend.medicines.management.commands.import_medicines_csv import money


def test_display_name_includes_pack_so_variants_stay_distinct():
    syrup = {"name": "AGLOZYME", "pack_size": "200 mL, syrup/bottle"}
    caps = {"name": "AGLOZYME", "pack_size": "10  capsule/strip"}
    assert display_name(syrup) == "Aglozyme 200 Ml, Syrup/Bottle"
    assert display_name(syrup) != display_name(caps)


def test_description_falls_back_to_composition():
    assert description_of(
        {"description_json": '{"introduction": "Relieves pain."}', "composition": "X"},
    ) == "Relieves pain."
    assert description_of({"description_json": "{}", "composition": "X 10mg"}) == "X 10mg"
    assert description_of({"description_json": "not json", "composition": "X"}) == "X"


def test_money_tolerates_blank_and_junk():
    assert money("34.0") == Decimal("34.00")
    assert money("") == Decimal("0.00")
    assert money("n/a") == Decimal("0.00")
