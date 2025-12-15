from decimal import Decimal
import re
import os
import pandas as pd

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from docatho_backend.medicines.models import Medicine, Category


def _clean_decimal(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return Decimal("0.00")
    s = str(value).strip()
    s = re.sub(r"[^\d\.\-]", "", s)
    if s == "" or s == "-":
        return Decimal("0.00")
    try:
        return Decimal(s)
    except Exception:
        return Decimal("0.00")


class Command(BaseCommand):
    help = "Import medicines from an Excel (xlsx) or CSV file. Columns expected: PRODUCT, PACK, CONTENT, MFG, MRP"

    def add_arguments(self, parser):
        parser.add_argument("xlsx_path", type=str, help="Path to the xlsx or csv file")
        parser.add_argument(
            "--sheet",
            type=str,
            default=None,
            help="Sheet name or number (pandas accepts either) - ignored for CSV",
        )
        parser.add_argument(
            "--category",
            type=str,
            default=None,
            help="Optional category name to assign all imported medicines to",
        )

    def handle(self, *args, **options):
        path = options["xlsx_path"]
        sheet = options["sheet"]
        category_name = options["category"]

        # Read file: support .xlsx/.xls and .csv
        try:
            if str(path).lower().endswith(".csv"):
                # read CSV as text to preserve columns exactly
                df = pd.read_csv(
                    path, dtype=str, encoding="utf-8", keep_default_na=False
                )
            else:
                df = pd.read_excel(path, sheet_name=sheet, dtype=str)
        except Exception as exc:
            self.stderr.write(f"Failed to read file: {exc}")
            return

        # normalize and drop empty/unnamed columns
        df.columns = [str(c).strip() for c in df.columns]
        df = df.loc[
            :, [c for c in df.columns if c and not str(c).lower().startswith("unnamed")]
        ]
        df.columns = [c.upper() for c in df.columns]

        created = 0
        updated = 0
        skipped = 0

        # optional category object
        cat = None
        if category_name:
            cat, _ = Category.objects.get_or_create(name=category_name)

        for idx, row in df.iterrows():
            product = row.get("PRODUCT") or row.get("PRODUCT NAME") or row.get("NAME")
            if not product or pd.isna(product) or str(product).strip() == "":
                skipped += 1
                continue
            name = str(product).strip()

            # Skip if a medicine with same name (case-insensitive) already exists
            if Medicine.objects.filter(name__iexact=name).exists():
                skipped += 1
                continue

            manufacturer = row.get("MFG") or row.get("MANUFACTURER")
            content = row.get("CONTENT")
            mrp = _clean_decimal(row.get("MRP") or row.get("PRICE") or 0)

            medicine, created_flag = Medicine.objects.get_or_create(
                name=name,
                defaults={
                    "manufacturer": (
                        str(manufacturer).strip()
                        if manufacturer and not pd.isna(manufacturer)
                        else None
                    ),
                    "content": (
                        str(content).strip()
                        if content and not pd.isna(content)
                        else None
                    ),
                    "price": mrp,
                },
            )
            if created_flag:
                created += 1
            else:
                changed = False
                if manufacturer and str(manufacturer).strip() != (
                    medicine.manufacturer or ""
                ):
                    medicine.manufacturer = str(manufacturer).strip()
                    changed = True
                if content and str(content).strip() != (medicine.content or ""):
                    medicine.content = str(content).strip()
                    changed = True
                if medicine.price != mrp:
                    medicine.price = mrp
                    changed = True
                if changed:
                    medicine.save()
                    updated += 1

            # # ensure slug
            # if not getattr(medicine, "slug", None):
            #     base = slugify(medicine.name)[:230]
            #     slug = base
            #     suffix = 1
            #     while (
            #         Medicine.objects.filter(slug=slug).exclude(pk=medicine.pk).exists()
            #     ):
            #         slug = f"{base}-{suffix}"
            #         suffix += 1
            #     medicine.slug = slug
            #     medicine.save()

            # attach category if provided
            if cat:
                medicine.category.add(cat)

        self.stdout.write(
            f"Imported. created={created} updated={updated} skipped={skipped} (rows={len(df)})"
        )
