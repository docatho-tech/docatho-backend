"""Ingest the real catalogue from the scraped CSVs into Category + Medicine.

Two files, joined on the source `id`:
  medicines_detailed.csv  — name, manufacturer, pack, price, composition, images,
                            long-form description_json
  medicines.csv           — therapeutic_category and drug schedule

    python manage.py import_medicines_csv                      # repo-root CSVs
    python manage.py import_medicines_csv --limit 200          # smoke test
    python manage.py import_medicines_csv --detailed a.csv --basic b.csv

Idempotent: re-running updates in place, keyed on (name, manufacturer).
"""

import csv
import json
import sys
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction

from docatho_backend.medicines.models import Category
from docatho_backend.medicines.models import DrugSchedule
from docatho_backend.medicines.models import Medicine

REPO_ROOT = Path(__file__).resolve().parents[5]

# therapeutic_category slug -> display name. Anything not listed falls back to
# slug.title(), so a new slug in the source data still imports.
CATEGORY_LABELS = {
    "cns": "Neurology & CNS",
    "gastrointestinal": "Stomach & Digestion",
    "analgesic": "Pain Relief",
    "antibiotic": "Antibiotics",
    "supplement": "Vitamins & Supplements",
    "dermatology": "Skin Care",
    "respiratory": "Respiratory Care",
    "antidiabetic": "Diabetic Care",
    "cardiovascular": "Heart & Blood Pressure",
    "antifungal": "Antifungals",
    "antihistamine": "Allergy & Antihistamines",
    "corticosteroid": "Corticosteroids",
    "ophthalmic": "Eye Care",
    "hormone": "Hormones",
    "antiparasitic": "Antiparasitics",
    "anticoagulant": "Blood Thinners",
    "antiviral": "Antivirals",
    "antitubercular": "Anti-Tubercular",
    "immunosuppressant": "Immunosuppressants",
    "oncology": "Oncology",
    "urology": "Urology",
    "electrolyte": "Electrolytes & Rehydration",
    "enzyme": "Enzymes",
    "ayurvedic": "Ayurvedic",
    "other": "Other",
    "unknown": "Uncategorised",
}

# Source `schedule` column -> DrugSchedule. NRX is the source's label for a
# non-narcotic prescription drug, i.e. Schedule H — both gate checkout on an Rx.
SCHEDULE_MAP = {
    "OTC": DrugSchedule.OTC,
    "H": DrugSchedule.H,
    "H1": DrugSchedule.H1,
    "NRX": DrugSchedule.H,
    "X": DrugSchedule.X,
}


def money(value: str) -> Decimal:
    try:
        return Decimal(value or "0").quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def description_of(row: dict) -> str:
    """Prefer the written introduction; fall back to the salt composition."""
    raw = (row.get("description_json") or "").strip()
    if raw:
        try:
            intro = (json.loads(raw).get("introduction") or "").strip()
        except (ValueError, AttributeError):
            intro = ""
        if intro:
            return intro
    return (row.get("composition") or "").strip()


def display_name(row: dict) -> str:
    """"ACE" + "10 tablet/strip" -> "Ace 10 Tablet/Strip".

    Pack size belongs in the name: ~1000 products share a name+manufacturer and
    differ only by pack, and the app has no pack_size field to tell them apart.
    """
    name = (row["name"] or "").strip().title()
    pack = (row["pack_size"] or "").strip().title()
    return f"{name} {pack}".strip()[:255]


class Command(BaseCommand):
    help = "Import medicines and their categories from the catalogue CSVs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--detailed",
            default=str(REPO_ROOT / "medicines_detailed.csv"),
            help="Path to medicines_detailed.csv",
        )
        parser.add_argument(
            "--basic",
            default=str(REPO_ROOT / "medicines.csv"),
            help="Path to medicines.csv (source of category + schedule)",
        )
        parser.add_argument("--limit", type=int, help="Import at most N rows")
        parser.add_argument(
            "--stock",
            type=int,
            default=100,
            help="Opening stock for every imported product (default 100).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        csv.field_size_limit(sys.maxsize)  # description_json fields are huge

        detailed_path = Path(options["detailed"])
        basic_path = Path(options["basic"])
        for path in (detailed_path, basic_path):
            if not path.exists():
                msg = f"CSV not found: {path}"
                raise CommandError(msg)

        # id -> (category slug, schedule), the only two columns we need from the
        # basic file. ~19k small tuples, cheap to hold.
        meta = {}
        with basic_path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                meta[row["id"]] = (
                    (row.get("therapeutic_category") or "unknown").strip().lower(),
                    (row.get("schedule") or "OTC").strip().upper(),
                )

        categories = {}
        for slug in sorted({slug for slug, _ in meta.values()} | {"unknown"}):
            categories[slug], _ = Category.objects.get_or_create(
                name=CATEGORY_LABELS.get(slug, slug.title()),
                defaults={"is_active": True},
            )

        created = updated = skipped = 0
        with detailed_path.open(encoding="utf-8") as fh:
            for index, row in enumerate(csv.DictReader(fh)):
                if options["limit"] and index >= options["limit"]:
                    break

                name = display_name(row)
                if not name:
                    skipped += 1
                    continue

                slug, schedule = meta.get(row["id"], ("unknown", "OTC"))
                manufacturer = (row.get("manufacturer") or "").strip().title()[:255]

                medicine, was_created = Medicine.objects.update_or_create(
                    name=name,
                    manufacturer=manufacturer,
                    defaults={
                        "brand": (row["name"] or "").strip().title()[:255],
                        "content": (row.get("composition") or "").strip(),
                        "description": description_of(row),
                        "image_url": (row.get("image_url") or "").strip(),
                        "price": money(row.get("selling_price")),
                        "mrp": money(row.get("mrp")),
                        "stock": options["stock"],
                        # Medicine.save() derives is_prescription_required.
                        "schedule": SCHEDULE_MAP.get(schedule, DrugSchedule.OTC),
                        "is_active": True,
                    },
                )
                medicine.category.set([categories[slug]])
                created += int(was_created)
                updated += int(not was_created)

                if (index + 1) % 2000 == 0:
                    self.stdout.write(f"  ...{index + 1} rows")

        rx = Medicine.objects.filter(is_prescription_required=True).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported. categories: {len(categories)} · "
                f"medicines: {created} new, {updated} updated, {skipped} skipped",
            ),
        )
        self.stdout.write(f"  prescription-only: {rx}")
