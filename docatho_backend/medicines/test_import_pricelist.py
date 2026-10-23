"""The rules the price-list importer gets wrong silently if they regress.

Every case here is a defect caught while importing the real sheet, not a
hypothetical: the substring collisions and filler values below each put wrong
data in the catalogue once already.
"""

from decimal import Decimal

import pytest

from docatho_backend.medicines.management.commands.import_pricelist_xlsx import DrugSchedule
from docatho_backend.medicines.management.commands.import_pricelist_xlsx import categorise
from docatho_backend.medicines.management.commands.import_pricelist_xlsx import description_for
from docatho_backend.medicines.management.commands.import_pricelist_xlsx import form_of
from docatho_backend.medicines.management.commands.import_pricelist_xlsx import money
from docatho_backend.medicines.management.commands.import_pricelist_xlsx import pack_count
from docatho_backend.medicines.management.commands.import_pricelist_xlsx import salt_of
from docatho_backend.medicines.management.commands.import_pricelist_xlsx import schedule_for


@pytest.mark.parametrize(
    ("name", "salt", "manufacturer", "expected"),
    [
        # PARACETAMOL contains "RACETAM"; a bare -racetam stem sent every
        # paracetamol combination to Neurology.
        ("FENAC-P TAB", "DICLOFENAC+PARACETAMOL", "ABARIS", "Pain Relief"),
        ("PIRACETAM TAB", "PIRACETAM", "INTAS", "Neurology & CNS"),
        # Salt suffixes have to carry the long tail of combinations.
        ("TELMA-AM TAB", "TELMISARTAN+AMLODIPINE", "GLENMARK", "Heart & Blood Pressure"),
        # A statin+aspirin polypill is cardiology, so the cardiovascular stems
        # are consulted before the antiplatelet list; aspirin on its own is not.
        ("ROSUVAS 10", "ROSUVASTATIN+ASPIRIN", "SUN PHARMA", "Heart & Blood Pressure"),
        ("ECOSPRIN 75", "ASPIRIN", "USV", "Blood Thinners"),
        ("GLYCOMET-GP", "GLIMEPIRIDE+METFORMIN HCL", "USV", "Diabetic Care"),
        # Clotrimazole has no "-conazole" in it.
        ("CANDID CREAM", "CLOTRIMAZOLE", "GLENMARK", "Antifungals"),
        # Cephalexin is spelled CEPH, not CEF.
        ("SPORIDEX CAP", "CEPHALEXIN", "CIPLA", "Antibiotics"),
        # No salt: the product name has to carry it.
        ("BP MACHINE MURCURY", "", "ERIKSON", "Surgical & Devices"),
        ("WHISPER ULTRA XL 6S", "", "1ST STEP", "Personal Care"),
        ("MAMY POKO PANTS XXL 7", "", "1ST STEP", "Child & Mother"),
        # "KID" used to match KIDNEY.
        ("KIDNEY CARE TAB", "", "AIMIL", "Other"),
        # Dose form is the weakest signal and must only catch what nothing else did.
        ("DERMIZIO-5 CREAM 15GM", "", "3M INDIA LTD.", "Skin Care"),
        # Neither salt nor name says anything — Other, not a guess.
        ("MDD-XR 100MG TAB", "", "ABBOTT HEALTHCARE PVT.LTD.,", "Other"),
        # The manufacturer's department is the last signal before giving up.
        ("FORCEPS ARTERY PLAIN", "", "SURGICAL DIVISION", "Surgical & Devices"),
        ("B-CHANDRAPRABHA VATI", "", "BAIDYANATH AYURVED LTD.", "Ayurvedic"),
    ],
)
def test_categorise(name, salt, manufacturer, expected):
    assert categorise(name, salt, manufacturer) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The billing software's filler values, which must never be stored as a
        # composition. "10% DEXTROSE" sits on sanitary pads and infant formula;
        # "COCAINE" is its mangling of "coconut" on a coconut-water row.
        ("10% DEXTROSE", ""),
        ("COCAINE", ""),
        ("...                         ", ""),
        ("GENERAL", ""),
        ("   PARACETAMOL   ", "PARACETAMOL"),
        ("TELMISARTAN+AMLODIPINE.", "TELMISARTAN+AMLODIPINE"),
    ],
)
def test_salt_of_drops_filler(raw, expected):
    assert salt_of(raw) == expected


@pytest.mark.parametrize(
    ("category", "salt", "expected"),
    [
        # Habit-forming and reserve-antibiotic salts are Schedule H1 whatever
        # shelf they were filed on.
        ("Mental Health", "CLONAZEPAM", DrugSchedule.H1),
        ("Antibiotics", "LEVOFLOXACIN", DrugSchedule.H1),
        ("Pain Relief", "TRAMADOL+PARACETAMOL", DrugSchedule.H1),
        # Prescription classes gate on an Rx even with no salt to go on.
        ("Antibiotics", "", DrugSchedule.H),
        ("Mental Health", "OLANZAPINE", DrugSchedule.H),
        ("Diabetic Care", "METFORMIN", DrugSchedule.H),
        # Things that are not prescription medicines at all.
        ("Personal Care", "", DrugSchedule.OTC),
        ("Surgical & Devices", "", DrugSchedule.OTC),
        ("Ayurvedic", "", DrugSchedule.OTC),
        ("Other", "", DrugSchedule.OTC),
    ],
)
def test_schedule_errs_towards_prescription(category, salt, expected):
    assert schedule_for(category, salt) == expected


def test_form_of_only_applies_to_things_that_are_dispensed():
    # "KNEE CAP" is a support. Nothing on the devices shelf has a dose form.
    assert form_of("KNEE CAP LARGE", "Surgical & Devices") == ""
    assert form_of("SPORIDEX CAP", "Antibiotics") == "capsules"
    assert form_of("EVA-Q SYP CHILD 100ML", "Respiratory Care") == "a syrup"
    # The more specific form has to win.
    assert form_of("MOXIGRAM EYE DROP 5ML", "Eye Care") == "eye drops"
    # A whole word, not a substring: "CAPSICUM" is not capsules.
    assert form_of("CAPSICUM PLASTER", "Pain Relief") == ""


def test_description_states_only_what_the_row_says():
    text = description_for(
        name="Adnorif-550Mg Tab",
        manufacturer="Aaa Pharma Pvt.Ltd.",
        category="Antibiotics",
        salt="RIFAXIMIN",
        pack=10,
        schedule=DrugSchedule.H,
    )
    assert text == (
        "Adnorif-550Mg Tab is an antibiotic from Aaa Pharma Pvt.Ltd. "
        "Composition: RIFAXIMIN. Supplied as tablets, 10 per pack. "
        "Prescription medicine — a valid prescription is required."
    )
    # No salt in the sheet means no composition sentence invented for it.
    assert "Composition" not in description_for(
        name="Bp Machine Murcury",
        manufacturer="Erikson",
        category="Surgical & Devices",
        salt="",
        pack=1,
        schedule=DrugSchedule.OTC,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("10", 10), ("10'S", 10), ("1", 1), ("", 1), (None, 1), ("  15  ", 15)],
)
def test_pack_count(raw, expected):
    assert pack_count(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(329.06, Decimal("329.06")), (0, Decimal("0.00")), (None, Decimal("0.00")), ("", Decimal("0.00"))],
)
def test_money(raw, expected):
    assert money(raw) == expected
