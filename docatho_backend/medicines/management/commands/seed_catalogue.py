"""Seed a realistic pharmacy catalogue for development and demos.

Gives a fresh install enough products to exercise pagination, search, the
category picker, stock warnings and — most importantly — the Schedule H/H1/X
prescription gate at checkout, which is unreachable with an empty catalogue.

Idempotent: re-running updates prices/stock and leaves ids alone, so it is
safe against a database you have already clicked around in.

    python manage.py seed_catalogue
    python manage.py seed_catalogue --stock-out 8   # more zero-stock rows

NOTE: schedules here are indicative test data chosen so every branch of the Rx
gate has rows behind it. They are not a regulatory reference — real catalogue
data must come from the supplier's item master, not from this file.
"""

from decimal import Decimal
from urllib.parse import quote

from django.core.management.base import BaseCommand
from django.db import transaction

from docatho_backend.medicines.models import Category
from docatho_backend.medicines.models import DrugSchedule
from docatho_backend.medicines.models import Medicine

# (name, description)
CATEGORIES = [
    ("Pain Relief", "Analgesics, antipyretics and anti-inflammatories."),
    ("Antibiotics", "Prescription-only antibacterials."),
    ("Diabetic Care", "Oral antidiabetics, insulin and monitoring supplies."),
    ("Heart & Blood Pressure", "Antihypertensives, statins and blood thinners."),
    ("Stomach & Digestion", "Antacids, PPIs and digestive aids."),
    ("Cold, Cough & Fever", "Antihistamines, decongestants and cough preparations."),
    ("Vitamins & Supplements", "Multivitamins, minerals and nutritional support."),
    ("Skin Care", "Topical antiseptics, antifungals and dermatological creams."),
    ("Respiratory Care", "Inhalers, bronchodilators and allergy control."),
    ("Child & Mother", "Paediatric formulations and maternal care."),
    ("First Aid", "Dressings, antiseptics and everyday injury care."),
    ("Mental Health", "Anxiolytics, antidepressants and sleep aids."),
]

# (name, brand, manufacturer, content, category, schedule, price, mrp, stock)
MEDICINES = [
    # --- Pain Relief ---
    ("Paracetamol 500mg Tablet", "Crocin", "GSK", "Paracetamol 500mg", "Pain Relief", "OTC", "28.00", "32.00", 480),
    ("Paracetamol 650mg Tablet", "Dolo 650", "Micro Labs", "Paracetamol 650mg", "Pain Relief", "OTC", "30.00", "34.00", 620),
    ("Ibuprofen 400mg Tablet", "Brufen", "Abbott", "Ibuprofen 400mg", "Pain Relief", "OTC", "42.00", "48.00", 210),
    ("Aspirin 75mg Tablet", "Ecosprin", "USV", "Aspirin 75mg", "Pain Relief", "OTC", "12.00", "14.50", 340),
    ("Diclofenac 50mg Tablet", "Voveran", "Novartis", "Diclofenac Sodium 50mg", "Pain Relief", "H", "38.00", "44.00", 155),
    ("Aceclofenac + Paracetamol Tablet", "Zerodol-P", "Ipca", "Aceclofenac 100mg + Paracetamol 325mg", "Pain Relief", "H", "88.00", "99.00", 190),
    ("Tramadol 50mg Capsule", "Ultracet", "Janssen", "Tramadol 50mg", "Pain Relief", "H1", "115.00", "129.00", 45),
    ("Diclofenac Gel 30g", "Volini", "Sun Pharma", "Diclofenac Diethylamine 1.16%", "Pain Relief", "OTC", "128.00", "145.00", 0),

    # --- Antibiotics ---
    ("Amoxicillin 500mg Capsule", "Mox", "Ranbaxy", "Amoxicillin 500mg", "Antibiotics", "H", "68.00", "76.00", 230),
    ("Amoxicillin + Clavulanate 625mg", "Augmentin 625", "GSK", "Amoxicillin 500mg + Clavulanic Acid 125mg", "Antibiotics", "H", "192.00", "215.00", 140),
    ("Azithromycin 500mg Tablet", "Azithral 500", "Alembic", "Azithromycin 500mg", "Antibiotics", "H", "98.00", "112.00", 175),
    ("Ciprofloxacin 500mg Tablet", "Ciplox 500", "Cipla", "Ciprofloxacin 500mg", "Antibiotics", "H1", "72.00", "82.00", 96),
    ("Levofloxacin 500mg Tablet", "Levoflox", "Cipla", "Levofloxacin 500mg", "Antibiotics", "H1", "105.00", "118.00", 64),
    ("Doxycycline 100mg Capsule", "Doxt", "Sun Pharma", "Doxycycline 100mg", "Antibiotics", "H", "58.00", "66.00", 120),
    ("Metronidazole 400mg Tablet", "Flagyl 400", "Abbott", "Metronidazole 400mg", "Antibiotics", "H", "44.00", "50.00", 165),
    ("Cefixime 200mg Tablet", "Taxim-O 200", "Alkem", "Cefixime 200mg", "Antibiotics", "H", "134.00", "150.00", 88),

    # --- Diabetic Care ---
    ("Metformin 500mg Tablet", "Glycomet 500", "USV", "Metformin HCl 500mg", "Diabetic Care", "H", "34.00", "39.00", 410),
    ("Metformin 1000mg Tablet", "Glycomet 1000", "USV", "Metformin HCl 1000mg", "Diabetic Care", "H", "56.00", "64.00", 275),
    ("Glimepiride 2mg Tablet", "Amaryl 2", "Sanofi", "Glimepiride 2mg", "Diabetic Care", "H", "88.00", "98.00", 130),
    ("Sitagliptin 100mg Tablet", "Januvia 100", "MSD", "Sitagliptin 100mg", "Diabetic Care", "H", "425.00", "478.00", 52),
    ("Human Insulin 40IU Vial", "Huminsulin", "Eli Lilly", "Human Insulin 40IU/ml", "Diabetic Care", "H", "165.00", "182.00", 38),
    ("Blood Glucose Test Strips (50)", "Accu-Chek Active", "Roche", "Glucose oxidase test strips", "Diabetic Care", "OTC", "985.00", "1120.00", 42),
    ("Glucometer Kit", "Accu-Chek Active", "Roche", "Glucometer with 10 strips", "Diabetic Care", "OTC", "1450.00", "1699.00", 18),

    # --- Heart & Blood Pressure ---
    ("Amlodipine 5mg Tablet", "Amlopres 5", "Cipla", "Amlodipine 5mg", "Heart & Blood Pressure", "H", "42.00", "48.00", 320),
    ("Telmisartan 40mg Tablet", "Telma 40", "Glenmark", "Telmisartan 40mg", "Heart & Blood Pressure", "H", "112.00", "126.00", 245),
    ("Losartan 50mg Tablet", "Losar 50", "Unichem", "Losartan Potassium 50mg", "Heart & Blood Pressure", "H", "78.00", "88.00", 180),
    ("Atorvastatin 10mg Tablet", "Atorva 10", "Zydus", "Atorvastatin 10mg", "Heart & Blood Pressure", "H", "94.00", "106.00", 290),
    ("Rosuvastatin 10mg Tablet", "Rosuvas 10", "Sun Pharma", "Rosuvastatin 10mg", "Heart & Blood Pressure", "H", "148.00", "165.00", 135),
    ("Metoprolol 50mg Tablet", "Metolar 50", "Cipla", "Metoprolol Succinate 50mg", "Heart & Blood Pressure", "H", "86.00", "97.00", 160),
    ("Clopidogrel 75mg Tablet", "Clopilet 75", "Sun Pharma", "Clopidogrel 75mg", "Heart & Blood Pressure", "H", "124.00", "140.00", 0),
    ("Warfarin 5mg Tablet", "Warf 5", "Cipla", "Warfarin Sodium 5mg", "Heart & Blood Pressure", "H", "68.00", "78.00", 55),

    # --- Stomach & Digestion ---
    ("Pantoprazole 40mg Tablet", "Pan 40", "Alkem", "Pantoprazole 40mg", "Stomach & Digestion", "H", "108.00", "122.00", 380),
    ("Omeprazole 20mg Capsule", "Omez 20", "Dr Reddy's", "Omeprazole 20mg", "Stomach & Digestion", "H", "62.00", "72.00", 265),
    ("Rabeprazole + Domperidone", "Razo-D", "Dr Reddy's", "Rabeprazole 20mg + Domperidone 30mg", "Stomach & Digestion", "H", "138.00", "155.00", 145),
    ("Antacid Suspension 200ml", "Digene", "Abbott", "Magnesium Hydroxide + Simethicone", "Stomach & Digestion", "OTC", "142.00", "160.00", 210),
    ("Ondansetron 4mg Tablet", "Emeset 4", "Cipla", "Ondansetron 4mg", "Stomach & Digestion", "H", "46.00", "54.00", 175),
    ("ORS Powder Sachet", "Electral", "FDC", "Oral Rehydration Salts WHO formula", "Stomach & Digestion", "OTC", "22.00", "25.00", 640),
    ("Lactulose Solution 200ml", "Duphalac", "Abbott", "Lactulose 10g/15ml", "Stomach & Digestion", "OTC", "195.00", "220.00", 95),

    # --- Cold, Cough & Fever ---
    ("Cetirizine 10mg Tablet", "Cetzine", "GSK", "Cetirizine 10mg", "Cold, Cough & Fever", "H", "28.00", "33.00", 420),
    ("Levocetirizine 5mg Tablet", "Levocet", "Sun Pharma", "Levocetirizine 5mg", "Cold, Cough & Fever", "H", "48.00", "55.00", 310),
    ("Montelukast + Levocetirizine", "Montair-LC", "Cipla", "Montelukast 10mg + Levocetirizine 5mg", "Cold, Cough & Fever", "H", "185.00", "208.00", 165),
    ("Cough Syrup 100ml", "Benadryl", "Johnson & Johnson", "Diphenhydramine + Ammonium Chloride", "Cold, Cough & Fever", "H", "132.00", "148.00", 185),
    ("Codeine Cough Syrup 100ml", "Corex", "Pfizer", "Codeine Phosphate + Chlorpheniramine", "Cold, Cough & Fever", "H1", "148.00", "168.00", 26),
    ("Paracetamol + Phenylephrine + CPM", "Sinarest", "Centaur", "Paracetamol 500mg + Phenylephrine 10mg + CPM 2mg", "Cold, Cough & Fever", "OTC", "78.00", "88.00", 290),
    ("Steam Inhalant Capsules", "Karvol Plus", "Reckitt", "Menthol + Eucalyptus oil", "Cold, Cough & Fever", "OTC", "92.00", "104.00", 140),

    # --- Vitamins & Supplements ---
    ("Vitamin D3 60000 IU Sachet", "Calcirol", "Cadila", "Cholecalciferol 60000 IU", "Vitamins & Supplements", "OTC", "34.00", "39.00", 520),
    ("Multivitamin Capsule", "Revital H", "Sun Pharma", "Multivitamin + Multimineral + Ginseng", "Vitamins & Supplements", "OTC", "295.00", "330.00", 245),
    ("Calcium + Vitamin D3 Tablet", "Shelcal 500", "Torrent", "Calcium Carbonate 500mg + Vitamin D3 250IU", "Vitamins & Supplements", "OTC", "118.00", "132.00", 380),
    ("Iron + Folic Acid Tablet", "Livogen", "Merck", "Ferrous Fumarate + Folic Acid", "Vitamins & Supplements", "OTC", "68.00", "78.00", 265),
    ("Vitamin B Complex Capsule", "Becosules", "Pfizer", "Vitamin B Complex + Vitamin C", "Vitamins & Supplements", "OTC", "52.00", "60.00", 410),
    ("Vitamin C 500mg Chewable", "Limcee", "Abbott", "Ascorbic Acid 500mg", "Vitamins & Supplements", "OTC", "38.00", "44.00", 350),
    ("Protein Powder 400g", "Protinex", "Danone", "Protein hydrolysate blend", "Vitamins & Supplements", "OTC", "545.00", "615.00", 0),

    # --- Skin Care ---
    ("Povidone Iodine Solution 100ml", "Betadine", "Win-Medicare", "Povidone Iodine 5%", "Skin Care", "OTC", "148.00", "165.00", 195),
    ("Clotrimazole Cream 15g", "Candid", "Glenmark", "Clotrimazole 1%", "Skin Care", "OTC", "88.00", "98.00", 220),
    ("Mupirocin Ointment 5g", "T-Bact", "GSK", "Mupirocin 2%", "Skin Care", "H", "128.00", "142.00", 130),
    ("Calamine Lotion 100ml", "Lacto Calamine", "Piramal", "Calamine + Zinc Oxide", "Skin Care", "OTC", "165.00", "185.00", 175),
    ("Ketoconazole Shampoo 100ml", "Nizral", "Janssen", "Ketoconazole 2%", "Skin Care", "H", "285.00", "320.00", 85),
    ("Sunscreen SPF 50 Gel 50g", "La Shield", "Glenmark", "SPF 50 PA+++ broad spectrum", "Skin Care", "OTC", "545.00", "610.00", 65),

    # --- Respiratory Care ---
    ("Salbutamol Inhaler 200 MDI", "Asthalin", "Cipla", "Salbutamol 100mcg/dose", "Respiratory Care", "H", "142.00", "158.00", 120),
    ("Budesonide + Formoterol Inhaler", "Foracort 200", "Cipla", "Budesonide 200mcg + Formoterol 6mcg", "Respiratory Care", "H", "465.00", "520.00", 72),
    ("Montelukast 10mg Tablet", "Montair 10", "Cipla", "Montelukast 10mg", "Respiratory Care", "H", "165.00", "185.00", 210),
    ("Nebuliser Solution 2.5ml", "Duolin Respules", "Cipla", "Levosalbutamol + Ipratropium", "Respiratory Care", "H", "112.00", "125.00", 95),
    ("Pulse Oximeter", "Dr Trust", "Nureca", "Fingertip SpO2 and pulse monitor", "Respiratory Care", "OTC", "1285.00", "1499.00", 34),

    # --- Child & Mother ---
    ("Paracetamol Syrup 60ml", "Calpol 250", "GSK", "Paracetamol 250mg/5ml", "Child & Mother", "OTC", "48.00", "55.00", 330),
    ("Zinc + ORS Paediatric Drops", "Zinconia", "Cipla", "Zinc Sulphate 20mg/5ml", "Child & Mother", "OTC", "62.00", "72.00", 185),
    ("Infant Formula Stage 1 400g", "Lactogen 1", "Nestle", "Infant milk substitute", "Child & Mother", "OTC", "425.00", "478.00", 120),
    ("Prenatal Multivitamin Tablet", "Folvite", "Pfizer", "Folic Acid 5mg", "Child & Mother", "OTC", "38.00", "44.00", 265),
    ("Baby Diapers Medium (44)", "Pampers", "P&G", "Medium size, 7-12kg", "Child & Mother", "OTC", "749.00", "849.00", 88),
    ("Gripe Water 130ml", "Woodward's", "Dabur", "Dill oil + Sodium bicarbonate", "Child & Mother", "OTC", "112.00", "125.00", 210),

    # --- First Aid ---
    ("Adhesive Bandages (20)", "Band-Aid", "Johnson & Johnson", "Sterile adhesive strips", "First Aid", "OTC", "85.00", "95.00", 420),
    ("Antiseptic Liquid 200ml", "Dettol", "Reckitt", "Chloroxylenol 4.8%", "First Aid", "OTC", "128.00", "145.00", 380),
    ("Cotton Roll 100g", "Ala Cotton", "Ala Surgicals", "Absorbent cotton wool IP", "First Aid", "OTC", "72.00", "82.00", 265),
    ("Crepe Bandage 10cm", "Dynamic", "Dynamic Techno", "Elastic crepe bandage", "First Aid", "OTC", "115.00", "130.00", 155),
    ("Digital Thermometer", "Dr Trust", "Nureca", "Digital clinical thermometer", "First Aid", "OTC", "245.00", "285.00", 96),
    ("Hand Sanitizer 500ml", "Lifebuoy", "HUL", "Ethyl alcohol 70% v/v", "First Aid", "OTC", "185.00", "210.00", 340),

    # --- Mental Health ---
    ("Alprazolam 0.5mg Tablet", "Alprax 0.5", "Torrent", "Alprazolam 0.5mg", "Mental Health", "H1", "48.00", "56.00", 32),
    ("Sertraline 50mg Tablet", "Daxid 50", "Sun Pharma", "Sertraline 50mg", "Mental Health", "H", "142.00", "160.00", 78),
    ("Escitalopram 10mg Tablet", "Nexito 10", "Sun Pharma", "Escitalopram 10mg", "Mental Health", "H", "128.00", "145.00", 92),
    ("Clonazepam 0.5mg Tablet", "Rivotril 0.5", "Abbott", "Clonazepam 0.5mg", "Mental Health", "H1", "58.00", "66.00", 28),
    ("Melatonin 5mg Tablet", "Meloset 5", "Aristo", "Melatonin 5mg", "Mental Health", "OTC", "185.00", "210.00", 115),
]


def placeholder_image(name: str) -> str:
    """A visibly synthetic image so the app's image path is exercised.

    Real product photography has to come from the supplier or manufacturer;
    a stand-in that obviously reads as a stand-in is safer here than one that
    could be mistaken for a real product shot.
    """
    return f"https://placehold.co/600x600/e8f0ec/1f2937?text={quote(name[:40])}"


class Command(BaseCommand):
    help = "Seed a realistic pharmacy catalogue for development and demos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--stock-out",
            type=int,
            default=0,
            help="Force this many extra products to zero stock (tests the out-of-stock path).",
        )
        parser.add_argument(
            "--no-images",
            action="store_true",
            help="Leave image_url blank instead of using placeholders.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        categories = {}
        cats_created = 0
        for name, description in CATEGORIES:
            category, created = Category.objects.get_or_create(
                name=name,
                defaults={"description": description, "is_active": True},
            )
            categories[name] = category
            cats_created += int(created)

        created = updated = 0
        zeroed = 0
        stock_out_budget = options["stock_out"]

        for row in MEDICINES:
            (
                name,
                brand,
                manufacturer,
                content,
                category_name,
                schedule,
                price,
                mrp,
                stock,
            ) = row

            if stock and stock_out_budget > 0:
                stock = 0
                stock_out_budget -= 1
                zeroed += 1

            defaults = {
                "brand": brand,
                "manufacturer": manufacturer,
                "content": content,
                "description": f"{content}. Marketed by {manufacturer}.",
                "price": Decimal(price),
                "mrp": Decimal(mrp),
                "stock": stock,
                # `Medicine.save()` derives is_prescription_required from this.
                "schedule": getattr(DrugSchedule, schedule),
                "is_active": True,
                "image_url": "" if options["no_images"] else placeholder_image(name),
            }

            # Name + manufacturer is the natural key: two firms sell
            # "Paracetamol 500mg Tablet" and they are different products.
            medicine, was_created = Medicine.objects.update_or_create(
                name=name,
                manufacturer=manufacturer,
                defaults=defaults,
            )
            medicine.category.set([categories[category_name]])
            created += int(was_created)
            updated += int(not was_created)

        rx_count = Medicine.objects.filter(is_prescription_required=True).count()
        out_of_stock = Medicine.objects.filter(stock=0).count()

        self.stdout.write(
            self.style.SUCCESS(
                f"Catalogue seeded. categories: {len(CATEGORIES)} "
                f"({cats_created} new) · medicines: {created} new, {updated} updated",
            ),
        )
        self.stdout.write(
            f"  prescription-only: {rx_count} · out of stock: {out_of_stock}"
            + (f" ({zeroed} forced)" if zeroed else ""),
        )
