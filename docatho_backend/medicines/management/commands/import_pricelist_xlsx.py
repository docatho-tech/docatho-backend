"""Ingest a pharmacy price-list spreadsheet into Category + Medicine.

The sheet is what a chemist's billing software exports — seven columns, three
banner rows, no therapeutic category and no drug schedule:

    PRODUCT | PACK | MFG | GENERIC | STOCK | MRP | TRADERATE

    python manage.py import_pricelist_xlsx JAI_PRICELST.xlsx
    python manage.py import_pricelist_xlsx JAI_PRICELST.xlsx --dry-run
    python manage.py import_pricelist_xlsx JAI_PRICELST.xlsx --limit 500

Idempotent: re-running updates in place, keyed on (name, manufacturer), the
same key `import_medicines_csv` uses.

Three things the source does not give us and this command has to derive:

**Category** — from the salt in GENERIC, then from form/product keywords in
PRODUCT. Salt suffixes do most of the work: one `-sartan` rule classifies every
telmisartan/olmesartan/losartan combination in the sheet, which is why the
tables below are morphemes rather than an exhaustive drug list.

**Schedule** — the sheet has no schedule column, so it is inferred from the drug
class and deliberately errs towards *requiring* a prescription. A systemic
antibiotic wrongly sold over the counter is a real harm; a cough syrup wrongly
asking for an Rx is an annoyance. Anything that is not identifiably a drug
(dressings, cosmetics, nutrition, ayurveda) stays OTC.

**Description** — assembled from the row's own fields. Nothing is invented: if
the sheet has no salt, the description does not claim one.

Rows priced at 0 are skipped. `Medicine.price` feeds `CartItem.unit_price`
directly, so a 0-priced row is a free checkout, not a catalogue placeholder.
"""

import re
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction

from docatho_backend.medicines.models import Category
from docatho_backend.medicines.models import DrugSchedule
from docatho_backend.medicines.models import Medicine

# GENERIC values the billing software uses as a filler rather than a salt.
# "10% Dextrose" sits on 384 live rows covering sanitary pads, wound dressings
# and infant formula, so it says nothing about the product and must never be
# stored as composition.
JUNK_GENERICS = {
    "",
    ".",
    "..",
    "...",
    "10% DEXTROSE",
    "GENERAL",
    "GENERAL ITEM",
    "CHEMICAL",
    "POWDER",
    # The sheet's mangling of "coconut" on a coconut-water row. A narcotic
    # printed as the composition of a soft drink is not a typo to pass on.
    "COCAINE",
}

# Categories, in match order. Each entry is (category name, (needles...)) and the
# needle is matched as a substring of the uppercased haystack, so "SARTAN"
# catches TELMISARTAN, OLMESARTAN MEDOXOMIL and TELMISARTAN+AMLODIPINE alike.
# Names match the categories already in the database; two are new (see NEW_).
SALT_RULES: list[tuple[str, tuple[str, ...]]] = [
    # --- non-drug labels the sheet puts in the GENERIC column -----------------
    ("Surgical & Devices", ("SURGICAL", "SYRINGE", "DRESSING", "CATHETER")),
    (
        "Personal Care",
        (
            "COSMETIC",
            "TOOTH PASTE",
            "TOOTHPASTE",
            "SANITARY NAPKIN",
            "FACE WASH",
            "FACE SERUM",
            "HAIR SERUM",
            "SHAMPOO",
            "SKIN BRIGHTENING",
            "MOISTURIZING FORMULA",
            "SUNSCREEN",
            "DEODORANT",
            "SOAP",
        ),
    ),
    ("Ayurvedic", ("AYURVED", "AYU + MEDH", "HERBAL")),
    (
        "Child & Mother",
        ("BABY CARE", "BABY MILK", "INFANT", "LACTATION"),
    ),
    # --- drug classes, by salt ------------------------------------------------
    ("Anti-Tubercular", ("RIFAMPICIN", "ISONIAZID", "ETHAMBUTOL", "PYRAZINAMIDE", "BEDAQUILINE")),
    (
        "Oncology",
        (
            "METHOTREXATE",
            "IMATINIB",
            "CAPECITABINE",
            "ANASTROZOLE",
            "LETROZOLE",
            "TAMOXIFEN",
            "BICALUTAMIDE",
            "CYCLOPHOSPHAMIDE",
            "ABIRATERONE",
            "SORAFENIB",
            "ERLOTINIB",
            "GEFITINIB",
            "PEMETREXED",
            "TEMOZOLOMIDE",
            "HYDROXYUREA",
            "MEGESTROL",
            "FLUTAMIDE",
            "THALIDOMIDE",
            "LENALIDOMIDE",
        ),
    ),
    (
        "Immunosuppressants",
        ("TACROLIMUS", "MYCOPHENOLATE", "CYCLOSPORIN", "AZATHIOPRINE", "TOFACITINIB", "SIROLIMUS"),
    ),
    (
        "Diabetic Care",
        (
            "GLIPTIN",
            "GLIFLOZIN",
            "GLIFOZIN",
            "METFORMIN",
            "GLIMEPIRIDE",
            "GLIMEPRIDE",
            "GLICLAZIDE",
            "VOGLIBOSE",
            "PIOGLITAZONE",
            "INSULIN",
            "SEMAGLUTIDE",
            "ACARBOSE",
            "GLIBENCLAMIDE",
            "REPAGLINIDE",
            "DULAGLUTIDE",
            "TIRZAPATIDE",
            "TIRZEPATIDE",
            "LIRAGLUTIDE",
            "SUGAR LEVEL",
        ),
    ),
    (
        "Heart & Blood Pressure",
        (
            "SARTAN",
            "STATIN",
            "DIPINE",
            "PRIL",
            "OLOL",
            "IVABRADINE",
            "SACUBITRIL",
            "TORSEMIDE",
            "FUROSEMIDE",
            "CHLORTHALIDONE",
            "HYDROCHLOROTHIAZIDE",
            "SPIRONOLACTONE",
            "DIGOXIN",
            "NITROGLYCERIN",
            "ISOSORBIDE",
            "RANOLAZINE",
            "TRIMETAZIDINE",
            "FENOFIBRATE",
            "EZETIMIBE",
            "CILNIDIPNE",
            "CILNIDIPINE",
            "EPLERENONE",
            "NICORANDIL",
            "DILTIAZEM",
            "VERAPAMIL",
            "CLONIDINE",
            "PRAZOSIN",
            "HYDRALAZINE",
            "GLYCERYL TRINITRATE",
            "INDAPAMIDE",
            "BOSENTAN",
            "DOBUTAMINE",
        ),
    ),
    (
        "Blood Thinners",
        (
            "CLOPIDOGREL",
            "ASPIRIN",
            "WARFARIN",
            "APIXABAN",
            "RIVAROXABAN",
            "TICAGRELOR",
            "DABIGATRAN",
            "PRASUGREL",
            "HEPARIN",
            "ENOXAPARIN",
            "ACENOCOUMAROL",
        ),
    ),
    (
        "Antifungals",
        ("CONAZOLE", "TERBINAFINE", "GRISEOFULVIN", "AMPHOTERICIN", "NYSTATIN", "CASPOFUNGIN", "AMOROLFINE", "SERTACONAZOLE", "EBERCONAZOLE"),
    ),
    (
        "Antivirals",
        (
            "ACICLOVIR",
            "ACYCLOVIR",
            "VALACYCLOVIR",
            "OSELTAMIVIR",
            "TENOFOVIR",
            "LAMIVUDINE",
            "SOFOSBUVIR",
            "FAVIPIRAVIR",
            "RITONAVIR",
            "MOLNUPIRAVIR",
            "ENTECAVIR",
        ),
    ),
    (
        "Antiparasitics",
        (
            "ALBENDAZOLE",
            "MEBENDAZOLE",
            "IVERMECTIN",
            "PERMETHRIN",
            "HYDROXYCHLOROQUINE",
            "CHLOROQUINE",
            "ARTEMETHER",
            "ARTESUNATE",
            "PRAZIQUANTEL",
            "DIETHYLCARBAMAZINE",
        ),
    ),
    (
        "Antibiotics",
        (
            "FLOXACIN",
            "CILLIN",
            "CEF",
            "PENEM",
            "MYCIN",
            "CYCLINE",
            "METRONIDAZOLE",
            "ORNIDAZOLE",
            "SECNIDAZOLE",
            "TINIDAZOLE",
            "NITROFURANTOIN",
            "FOSFOMYCIN",
            "LINEZOLID",
            "RIFAXIMIN",
            "CHLORAMPHENICOL",
            "COTRIMOXAZOLE",
            "SULFAMETHOXAZOLE",
            "TRIMETHOPRIM",
            "CLAVULANATE",
            "AMIKACIN",
            "GENTAMICIN",
            "COLISTIN",
            "VANCOMYCIN",
        ),
    ),
    (
        "Corticosteroids",
        (
            "PREDNISOLONE",
            "DEXAMETHASONE",
            "BETAMETHASONE",
            "HYDROCORTISONE",
            "MOMETASONE",
            "FLUTICASONE",
            "CLOBETASOL",
            "DEFLAZACORT",
            "BUDESONIDE",
            "TRIAMCINOLONE",
            "METHYLPREDNISOLONE",
            "BECLOMETHASONE",
            "HALOBETASOL",
        ),
    ),
    (
        "Mental Health",
        (
            "ZEPAM",
            "ZOLAM",
            "ETIZOLAM",
            "CLOBAZAM",
            "ZOPICLONE",
            "ZOLPIDEM",
            "ESCITALOPRAM",
            "CITALOPRAM",
            "SERTRALINE",
            "FLUOXETINE",
            "FLUVOXAMINE",
            "PAROXETINE",
            "VENLAFAXINE",
            "DESVENLAFAXINE",
            "DULOXETINE",
            "MIRTAZAPINE",
            "AMITRIPTYLINE",
            "NORTRIPTYLINE",
            "IMIPRAMINE",
            "OLANZAPINE",
            "QUETIAPINE",
            "RISPERIDONE",
            "ARIPIPRAZOLE",
            "AMISULPRIDE",
            "CLOZAPINE",
            "HALOPERIDOL",
            "LURASIDONE",
            "TRIFLUOPERAZINE",
            "FLUPENTIXOL",
            "MELITRACEN",
            "LITHIUM",
            "BUSPIRONE",
            "MELATONIN",
            "MODAFINIL",
            "ATOMOXETINE",
            "METHYLPHENIDATE",
            "TRIHEXYPHEN",
            "CHLORDIAZEPOXIDE",
            "SERTALINE",
            "DOTHIEPIN",
            "VORTIOXETINE",
            "CARIPRAZINE",
            "AGOMELATINE",
            "ZIPRASIDONE",
            "PALIPERIDONE",
            "BLONANSERIN",
            "NICOTINE",
        ),
    ),
    (
        "Neurology & CNS",
        (
            "PREGABALIN",
            "GABAPENTIN",
            "PIRACETAM",
            "ANIRACETAM",
            "LEVETIRACETAM",
            "BRIVARACETAM",
            "AZEPINE",
            "OXCARBAZEPINE",
            "CARBAMAZEPINE",
            "LAMOTRIGINE",
            "TOPIRAMATE",
            "VALPROATE",
            "DIVALPROEX",
            "PHENYTOIN",
            "PHENOBARBITONE",
            "DONEPEZIL",
            "MEMANTINE",
            "LEVODOPA",
            "PRAMIPEXOLE",
            "ROPINIROLE",
            "BACLOFEN",
            "TIZANIDINE",
            "BETAHISTINE",
            "SUMATRIPTAN",
            "RIZATRIPTAN",
            "FLUNARIZINE",
            "MECOBALAMIN",
            "METHYCOBALAMIN",
            "METHYLCOBALAMIN",
            "VINPOCETINE",
            "CITICOLINE",
            "PERAMPANEL",
            "CINNARIZINE",
            "TOLPERISONE",
            "ZONISAMIDE",
            "LACOSAMIDE",
            "ETHOSUXIMIDE",
            "ENTACAPONE",
            "SAFINAMIDE",
            "RILUZOLE",
            "ERGOTAMINE",
            "NARATRIPTAN",
            "BENFO",
        ),
    ),
    (
        "Stomach & Digestion",
        (
            "PRAZOLE",
            "VONOPRAZAN",
            "DOMPERIDONE",
            "ONDANSETRON",
            "LEVOSULPRIDE",
            "ITOPRIDE",
            "MOSAPRIDE",
            "PRUCALOPRIDE",
            "LACTULOSE",
            "ISPAGHULA",
            "BISACODYL",
            "SODIUM PICOSULPHATE",
            "LOPERAMIDE",
            "MESALAMINE",
            "MESALAZINE",
            "URSODEOXYCHOLIC",
            "SILYMARIN",
            "DICYCLOMINE",
            "DROTAVERINE",
            "SIMETHICONE",
            "SUCRALFATE",
            "ANTACID",
            "RANITIDINE",
            "FAMOTIDINE",
            "PROBIOTIC",
            "SACCHAROMYCES",
            "LACTOBACILLUS",
            "LACTOBACILLIS",
            "GLYCOPYRROLATE",
            "RIFAGUT",
            "LACTITOL",
            "POLYETHYLENE GLYCOL",
            "HYOSCINE",
            "MEBEVERINE",
            "RACECADOTRIL",
            "LIVER EXTRACT",
            "TEGASEROD",
            "ALUMINIUM HYDROXIDE",
            "MAGALDRATE",
            "OXETACAINE",
        ),
    ),
    (
        "Pain Relief",
        (
            "PARACETAMOL",
            "ACETAMINOPHEN",
            "ACECLOFENAC",
            "DICLOFENAC",
            "IBUPROFEN",
            "NAPROXEN",
            "ETORICOXIB",
            "ETODOLAC",
            "NIMESULIDE",
            "KETOROLAC",
            "PIROXICAM",
            "MEFENAMIC",
            "TRAMADOL",
            "THIOCOLCHICOSIDE",
            "CHLORZOXAZONE",
            "SERRATIOPEPTIDASE",
            "FEBUXOSTAT",
            "COLCHICINE",
            "ALLOPURINOL",
            "PAIN RELIVER",
            "PAIN RELIEVER",
            "DICLOFENC",
        ),
    ),
    (
        "Allergy & Antihistamines",
        (
            "CETIRIZINE",
            "CETRIZINE",
            "LEVOCETIRIZINE",
            "LEVOCETRIZINE",
            "FEXOFENADINE",
            "BILASTINE",
            "LORATADINE",
            "DESLORATADINE",
            "RUPATADINE",
            "EBASTINE",
            "HYDROXYZINE",
            "CHLORPHENIRAMINE",
            "PHENIRAMINE",
            "MONTELUKAST",
        ),
    ),
    (
        "Respiratory Care",
        (
            "SALBUTAMOL",
            "LEVOSALBUTAMOL",
            "FORMOTEROL",
            "SALMETEROL",
            "IPRATROPIUM",
            "TIOTROPIUM",
            "THEOPHYLLINE",
            "ACEBROPHYLLINE",
            "DOXOFYLLINE",
            "AMBROXOL",
            "BROMHEXINE",
            "GUAIPHENESIN",
            "GUAIFENESIN",
            "TERBUTALINE",
            "ACETYLCYSTEINE",
            "ACETYLCYSTINE",
            "DEXTROMETHORPHAN",
            "COUGH SYP",
            "COUGH SYRUP",
            "ACEBROFYLLINE",
            "GLYCOPYRRONIUM",
            "OXYMETAZOLINE",
            "XYLOMETAZOLINE",
            "MONTELUK",
            "BAMBUTEROL",
            "CARBOCISTEINE",
        ),
    ),
    (
        "Eye Care",
        (
            "NEPAFENAC",
            "BRIMONIDINE",
            "TIMOLOL",
            "LATANOPROST",
            "BIMATOPROST",
            "DORZOLAMIDE",
            "CARBOXY METHYLCELLULOSE",
            "CARBOXYMETHYLCELLULOSE",
            "SODIUM HYALURONATE",
            "HYPROMELLOSE",
            "OLOPATADINE",
            "KETOTIFEN",
        ),
    ),
    (
        "Urology",
        (
            "SILODOSIN",
            "TAMSULOSIN",
            "DUTASTERIDE",
            "FINASTERIDE",
            "ALFUZOSIN",
            "SOLIFENACIN",
            "DARIFENACIN",
            "MIRABEGRON",
            "TADALAFIL",
            "SILDENAFIL",
            "OXYBUTYNIN",
            "POTASSIUM CITRATE",
        ),
    ),
    (
        "Hormones",
        (
            "THYROXINE",
            "LEVOTHYROXINE",
            "CARBIMAZOLE",
            "METHIMAZOLE",
            "PROGESTERONE",
            "DYDROGESTERONE",
            "NORETHISTERONE",
            "ESTRADIOL",
            "ETHINYL",
            "TESTOSTERONE",
            "CABERGOLINE",
            "BROMOCRIPTINE",
            "LETROZOL",
            "CLOMIPHENE",
            "DESOGESTREL",
            "DROSPIRENONE",
            "ULIPRISTAL",
            "MIFEPRISTONE",
            "MISOPROSTOL",
            "HCG",
        ),
    ),
    (
        "Skin Care",
        (
            "ISOTRETINOIN",
            "TRETINOIN",
            "ADAPALENE",
            "BENZOYL PEROXIDE",
            "CLINDAMYCIN",
            "MUPIROCIN",
            "FUSIDIC",
            "MINOXIDIL",
            "GLYCOLIC",
            "SALICYLIC",
            "HYDROQUINONE",
            "KOJIC",
            "AZELAIC",
            "TRANEXAMIC",
            "CALAMINE",
            "UREA CREAM",
            "TACALCITOL",
            "CALCIPOTRIOL",
            "ZINC PYRITHIONE",
            "LOTION",
            "EMOLLIENT",
        ),
    ),
    (
        "Electrolytes & Rehydration",
        ("ORS", "ORAL REHYDRATION", "SODIUM CHLORIDE", "SODIUM BICARBONATE", "GLUCOSE", "DEXTROSE"),
    ),
    (
        "Enzymes",
        ("TRYPSIN", "BROMELAIN", "RUTOSIDE", "PANCREATIN", "PEPSIN", "AMYLASE", "PAPAIN"),
    ),
    (
        "Vitamins & Supplements",
        (
            "VITAMIN",
            "VITA-D",
            "CHOLECALCIFEROL",
            "CALCITRIOL",
            "CALCIUM",
            "FOLIC ACID",
            "FERROUS",
            "IRON",
            "ZINC",
            "MAGNESIUM",
            "MULTIVITAMIN",
            "MULTI VITA",
            "MULTIMINERAL",
            "MULTI MIN",
            "PROTEIN",
            "PROTIN",
            "L-CARNITINE",
            "L-ARGININE",
            "OMEGA-3",
            "OMEGA 3",
            "BIOTIN",
            "COENZYME",
            "NUTRACEUTICAL",
            "FOOD PRODUCT",
            "THIAMINE",
            "NICOTINAMIDE",
            "PYRIDOXINE",
            "ASCORBIC",
            "ANTIOXIDANT",
            "AMINO ACID",
            "COLLAGEN",
            "WHEY",
            "S-ADENOSYL",
            "L-GLUTAMINE",
            "MYO-INOSITOL",
            "INOSITOL",
            "ALPHA LIPOIC",
            "ALPHA LPC",
            "ALPHA KETOANALOGUE",
            "METHYLFLTE",
            "METHYLFOLATE",
            "METHYLCO",
            "MECO",
            "CRANBERRY",
            "D-MANOSE",
            "ASTAXANTHIN",
            "PALMITOYLETHANOLAMIDE",
            "ENERGY",
            "LYCOPENE",
            "LUTEIN",
            "GINSENG",
            "CURCUMIN",
            "SHILAJIT",
            "ASHWAGANDHA",
            "SOYA",
            "SELENIUM",
            "CHROMIUM",
            "POTASSIUM MAGNESIUM",
            "ELECTROLYTE",
        ),
    ),
    (
        "Hormones",
        ("TERIPARATIDE", "SOMATROPIN", "OCTREOTIDE", "DESMOPRESSIN", "FLUDROCORTISONE"),
    ),
    (
        "Electrolytes & Rehydration",
        ("POTASSIUM CHLORIDE", "SODIUM ACETATE", "CALCIUM GLUCONATE", "RINGER"),
    ),
    (
        "Skin Care",
        (
            "LACTIC ACID",
            "HYALURONIC",
            "ISOTERTINOIN",
            "NIACINAMIDE",
            "RETINOL",
            "CERAMIDE",
            "DIMETHICONE",
            "PODOPHYLLIN",
            "ELOCON",
            "SILICONE GEL",
            "SCAR",
        ),
    ),
    ("Urology", ("SILDENAFL", "VARDENAFIL", "DAPOXETINE", "TOLTERODINE", "FLAVOXATE")),
    ("Pain Relief", ("BALM", "DICLOFENC", "FLURBIPROFEN", "LORNOXICAM", "TAPENTADOL")),
    # The sheet is typed by hand, so a salt arrives spelled several ways
    # ("PARACITAMOL", "GLIMIPERIDE", "NITROFURANTON"). These are the misspellings
    # and the stems the rules above miss outright — clotrimazole has no
    # "-conazole" in it, and cephalexin is spelled "CEPH", not "CEF".
    ("Antifungals", ("IMAZOLE", "CLOTRIMAZOL")),
    ("Antibiotics", ("CEPH", "NITROFURANTON", "MINOCYLINE", "AZITHRO", "DOXY")),
    ("Blood Thinners", ("NICOUMALONE", "CILOSTAZOL", "DIPYRIDAMOLE")),
    ("Heart & Blood Pressure", ("CARVEDILOL", "MIDODRINE", "LABETALOL", "NEBIVOLOL")),
    (
        "Diabetic Care",
        ("GLIMIPERIDE", "GLIMIPRID", "GLIMEPRDE", "IMEGLIMIN", "SAROGLITAZAR", "GLICLAZID"),
    ),
    (
        "Stomach & Digestion",
        (
            "BACILLUS",
            "ISAPGOL",
            "ISABGOL",
            "LEVOSULPIRIDE",
            "ACOTIAMIDE",
            "LAXATIVE",
            "ANTIEMETIC",
        ),
    ),
    ("Allergy & Antihistamines", ("CYPROHEPTADINE", "DESLORATIDINE", "CETIRIZIN", "FEXOFENADIN")),
    ("Mental Health", ("CLOMIPRAMINE", "NALTREXONE", "ACAMPROSATE", "DISULFIRAM")),
    ("Neurology & CNS", ("PRIMIDONE", "PIRIBEDIL", "ROTIGOTINE")),
    ("Pain Relief", ("POLMACOXID", "PARACITAMOL", "PARACETAMO", "ACECLOFENC", "KETOPROFEN")),
    ("Respiratory Care", ("ACETYCYSTEINE", "ACETYLCYSTEIN")),
    ("Urology", ("BETHANECHOL", "TERAZOSIN")),
    ("Hormones", ("NANDROLONE", "STANOZOLOL", "OXANDROLONE")),
    ("First Aid", ("CHLORHEXIDINE", "POVIDONE", "HYDROGEN PEROXIDE")),
    ("Personal Care", ("HAIR DYE", "HAIR OIL", "HAIR COLOR", "HAIR COLOUR", "TALCUM")),
    (
        "Vitamins & Supplements",
        (
            "MULTIVIT",
            "MULTIMIN",
            "MULTI MIN",
            "MULTIMINE",
            "MINER",
            "ANTIOX",
            "CISSUS",
            "DIOSMIN",
            "FLAVONID",
            "LACTOFERIN",
            "L-TYROSINE",
            "LYSINE",
            "CARNITINE",
            "CARNI",
            "GUANOSINE",
            "EXTRACT",
        ),
    ),
]

# Fallbacks read off the PRODUCT name, for the ~3.5k rows with no usable salt.
# Checked after the salt rules so a medicated face wash lands in Skin Care
# rather than Personal Care.
NAME_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "Surgical & Devices",
        (
            "SYRINGE",
            "NEEDLE",
            "CANULA",
            "CANNULA",
            "GLOVE",
            "BP MACHINE",
            "BP MONITOR",
            "THERMOMETER",
            "GLUCOMETER",
            "NEBULIZER",
            "NEBULISER",
            "OXIMETER",
            "CATHETER",
            "FORCEPS",
            "SCISSOR",
            "SCALPEL",
            "SUTURE",
            "BANDAGE",
            "GAUZE",
            "COTTON",
            "CREPE",
            "TEGADERM",
            "MICROPORE",
            "URINE BAG",
            "DRIP SET",
            "IV SET",
            "MASK",
            "WHEEL CHAIR",
            "WALKER",
            "BELT",
            "CRUTCH",
            "KNEE CAP",
            "NEBULIZATION",
            "LANCET",
            "TEST STRIP",
            "WEIGHING",
            "STETHOSCOPE",
            "HOT WATER BAG",
            "ICE BAG",
            "CATHETOR",
            "EAR PLUG",
            "BATTERY",
            "GLUCOCARD",
            "ACCU CHEK",
            "ACCU-CHEK",
            "KNEE CAP",
            "KNEE SUPPORT",
            "ANKLE",
            "ELBOW",
            "BRACE",
            "SPLINT",
            "POSTURE",
            "ORTHO",
            "COLLAR",
            "ABDOMINAL",
            "LS BELT",
            "SLING",
            "NEBUL",
            "AIR BED",
            "COMMODE",
            "SUCTION",
            "FEEDING TUBE",
            "RYLES",
            "SPACER",
            "TOURNIQUET",
            "APRON",
            "CAP SURGEON",
            "SPIROMETER",
        ),
    ),
    (
        "Personal Care",
        (
            "FACE WASH",
            "FACEWASH",
            "SHAMPOO",
            "CONDITIONER",
            "SOAP",
            "TOOTH PASTE",
            "TOOTHPASTE",
            "TOOTH BRUSH",
            "TOOTHBRUSH",
            "DANT MANJ",
            "MOUTH WASH",
            "MOUTHWASH",
            "DEODORANT",
            "PERFUME",
            "TALC",
            "WHISPER",
            "STAYFREE",
            "SANITARY",
            "PERIOD PANTY",
            "DIAPER",
            "DUREX",
            "CONDOM",
            "KAMASUTRA",
            "SHAVING",
            "RAZOR",
            "HAIR OIL",
            "HAIR COLOR",
            "HAIR COLOUR",
            "LIP BALM",
            "NAIL",
            "SCRUB",
            "FACIAL",
            "SANITIZER",
            "HAND WASH",
            "WET WIPE",
            "TISSUE",
            "SENSODYNE",
            "COLGATE",
            "CLOSE UP",
            "CLOSEUP",
            "PEPSODENT",
            "ORAL B",
            "PEESAFE",
            "SEBAMED",
            "DOVE",
            "PONDS",
            "NIVEA",
            "VASELINE",
            "BODY WASH",
            "BODY LOTION",
            "MICELLAR",
            "TONER",
            "SUGARFREE",
            "SUGAR FREE",
            "STAYFREE",
            "SOFY",
            "TAMPON",
            "MENSTRUAL CUP",
            "HAIR REMOV",
            "WAX STRIP",
            "KAJAL",
            "LIPSTICK",
            "COMB",
        ),
    ),
    (
        "Child & Mother",
        (
            "FEEDING BOTTLE",
            "BABY",
            "INFANT",
            "MEE MEE",
            "PEDIASURE",
            "LACTOGEN",
            "NAN PRO",
            "CERELAC",
            "FARELAC",
            "DEXOLAC",
            "NIPPLE",
            "BREAST PUMP",
            "MAMA",
            "JUNIOR",
            "KIDS",
            "CHILD",
            "MAMY POKO",
            "PAMPERS",
            "HUGGIES",
            "TEETHER",
            "HOPOP",
            "CHICCO",
            "PIGEON",
            "FEEDER",
            "SIPPER",
            "MOTHER",
            "PRE NATAL",
            "PRENATAL",
        ),
    ),
    ("Eye Care", ("EYE DROP", "E/D", "EYE OINT", "LENS", "EYE SOL")),
    (
        "Cold, Cough & Fever",
        (
            "COLD",
            "COUGH",
            "GARGLE",
            "THROAT",
            "LOZENGE",
            "VAPORUB",
            "INHALER",
            "NASAL",
            "DECONGEST",
            "STEAM",
        ),
    ),
    (
        "First Aid",
        (
            "BURNOL",
            "ANTISEPTIC",
            "DETTOL",
            "SAVLON",
            "BETADINE",
            "BAND AID",
            "BANDAID",
            "SOFRAMYCIN",
            "BURN",
        ),
    ),
    (
        "Electrolytes & Rehydration",
        ("ELECTRAL", "PROLYTE", "REHYDRAT", "GLUCON"),
    ),
    (
        "Pain Relief",
        ("BALM", "VOLINI", "MOOV", "RELISPRAY", "IODEX", "PAIN", "SPRAIN", "KNEE OIL"),
    ),
    (
        "Vitamins & Supplements",
        (
            "PROTEIN",
            "ENSURE",
            "HORLICKS",
            "BOURNVITA",
            "SHAKE",
            "NUTRITION",
            "PEDIASURE",
            "SUPPLEMENT",
            "MULTIVIT",
            "CALCI",
            "ZINCO",
            "OMEGA",
            "SEEDS",
            "ROASTED",
            "MUSHROOM",
            "POWDER 100GM",
        ),
    ),
    (
        "Ayurvedic",
        (
            "CHURNA",
            "VATI",
            "ASAVA",
            "ARISHTA",
            "KWATH",
            "BHASMA",
            "GUGGULU",
            "TAILA",
            "ZANDU",
            "JALNETI",
            "CHYAWAN",
            "TRIPHALA",
            "GILOY",
            "TULSI",
            "NEEM",
            "ALOE",
        ),
    ),
    # Dose form, checked last: a product with no salt and no brand match that is
    # nonetheless a cream, lotion or ointment is a topical, and Skin Care is the
    # only shelf a topical belongs on. Weak signal by design — it only ever sees
    # rows every rule above has already passed over.
    (
        "Skin Care",
        ("CREAM", "LOTION", "OINT", "SERUM", "CLEANSER", "MOISTUR", "CETAPHIL", "SCRUB"),
    ),
]

# MFG is a billing-software department as often as a company, and the department
# names are a reliable last signal before giving up.
MFG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Surgical & Devices", ("SURGICAL", "DIAGNOSTIC DIVISION", "EQUIPMENT")),
    ("Personal Care", ("COSMETIC",)),
    ("Ayurvedic", ("AYURVED", "BAIDYANATH", "DABUR", "ZANDU", "PATANJALI", "HIMALAYA", "CHARAK")),
    ("Vitamins & Supplements", ("FOOD", "NUTRITION", "NUTRACEUTICAL")),
]

FALLBACK_CATEGORY = "Other"

# Categories this sheet needs that the database did not already have. Kept
# explicit so the storefront's two new tabs are a reviewed decision and not a
# side effect of a typo in the tables above.
NEW_CATEGORIES = {
    "Surgical & Devices": "Dressings, disposables, diagnostic devices and clinic equipment.",
    "Personal Care": "Everyday hygiene, skincare and grooming products sold off the shelf.",
}

# Categories whose contents are not prescription medicines at all.
OTC_CATEGORIES = {
    "Surgical & Devices",
    "Personal Care",
    "Ayurvedic",
    "Vitamins & Supplements",
    "Child & Mother",
    "First Aid",
    "Electrolytes & Rehydration",
    "Cold, Cough & Fever",
    "Other",
}

# Categories that are prescription-only as a class. Erring towards Rx: see the
# module docstring for why the asymmetry is deliberate.
RX_CATEGORIES = {
    "Antibiotics",
    "Antivirals",
    "Anti-Tubercular",
    "Antiparasitics",
    "Oncology",
    "Immunosuppressants",
    "Corticosteroids",
    "Mental Health",
    "Neurology & CNS",
    "Blood Thinners",
    "Diabetic Care",
    "Heart & Blood Pressure",
    "Hormones",
    "Urology",
}

# Schedule H1 — habit-forming drugs and the reserve antibiotics the Drugs &
# Cosmetics Rules single out for a separate register. Matched on the salt.
H1_SALTS = (
    "ALPRAZOLAM",
    "CHLORDIAZEPOXIDE",
    "CLONAZEPAM",
    "DIAZEPAM",
    "LORAZEPAM",
    "MIDAZOLAM",
    "NITRAZEPAM",
    "ZOLPIDEM",
    "ETIZOLAM",
    "CLOBAZAM",
    "TRAMADOL",
    "CODEINE",
    "BUPRENORPHINE",
    "PENTAZOCINE",
    "MOXIFLOXACIN",
    "LEVOFLOXACIN",
    "MEROPENEM",
    "FAROPENEM",
    "CEFPIROME",
    "CEFETAMET",
    "COLISTIN",
    "VANCOMYCIN",
    "LINEZOLID",
    "RIFAMPICIN",
    "ISONIAZID",
    "ETHAMBUTOL",
    "PYRAZINAMIDE",
)

# How each category is described in prose, so a generated description reads
# "a pain-relief medicine" rather than "a Pain Relief product".
CATEGORY_BLURB = {
    "Allergy & Antihistamines": "an anti-allergy medicine",
    "Anti-Tubercular": "an anti-tubercular medicine",
    "Antibiotics": "an antibiotic",
    "Antifungals": "an antifungal medicine",
    "Antiparasitics": "an antiparasitic medicine",
    "Antivirals": "an antiviral medicine",
    "Ayurvedic": "an ayurvedic preparation",
    "Blood Thinners": "a blood-thinning medicine",
    "Child & Mother": "a mother and baby care product",
    "Cold, Cough & Fever": "a cold, cough and fever remedy",
    "Corticosteroids": "a corticosteroid medicine",
    "Diabetic Care": "a diabetes care medicine",
    "Electrolytes & Rehydration": "a rehydration and electrolyte product",
    "Enzymes": "an enzyme preparation",
    "Eye Care": "an eye care medicine",
    "First Aid": "a first-aid product",
    "Heart & Blood Pressure": "a heart and blood-pressure medicine",
    "Hormones": "a hormonal medicine",
    "Immunosuppressants": "an immunosuppressant medicine",
    "Mental Health": "a mental-health medicine",
    "Neurology & CNS": "a neurological medicine",
    "Oncology": "an oncology medicine",
    "Other": "a healthcare product",
    "Pain Relief": "a pain-relief medicine",
    "Personal Care": "a personal care product",
    "Respiratory Care": "a respiratory care medicine",
    "Skin Care": "a skin care product",
    "Stomach & Digestion": "a digestive care medicine",
    "Surgical & Devices": "a surgical and healthcare device product",
    "Urology": "a urological medicine",
    "Vitamins & Supplements": "a nutritional supplement",
}

# Dose form read off the product name, used in the description so a row reads
# "Supplied as a syrup." Ordered: "EYE DROP" must beat the bare "DROP".
FORM_WORDS: list[tuple[str, str]] = [
    ("EYE DROP", "eye drops"),
    ("EAR DROP", "ear drops"),
    ("NASAL", "a nasal spray"),
    ("SYP", "a syrup"),
    ("SYRUP", "a syrup"),
    ("SUSP", "a suspension"),
    ("LIQ", "a liquid"),
    ("TAB", "tablets"),
    ("TABLET", "tablets"),
    ("CAP", "capsules"),
    ("CAPSULE", "capsules"),
    ("INJ", "an injection"),
    ("CREAM", "a cream"),
    ("OINT", "an ointment"),
    ("GEL", "a gel"),
    ("LOTION", "a lotion"),
    ("POWDER", "a powder"),
    ("POW", "a powder"),
    ("SACHET", "sachets"),
    ("DROP", "drops"),
    ("SOLN", "a solution"),
    ("SPRAY", "a spray"),
    ("KIT", "a kit"),
    ("SHAMPOO", "a shampoo"),
    ("SOAP", "a soap"),
]

# Shelves whose products are not dispensed in a dose form, so the form words
# above must not be read off their names.
FORMLESS_CATEGORIES = {"Surgical & Devices", "Personal Care", "Child & Mother"}

# A name that is one character typed over and over ("ZZZZZZZZ") is a placeholder
# row in the shop's software, not a product.
JUNK_NAME = re.compile(r"^(.)\1{3,}$")


def money(value) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def clean(value) -> str:
    """Trim the fixed-width padding the export pads every cell with."""
    return re.sub(r"\s+", " ", str(value or "").strip())


def salt_of(raw: str) -> str:
    """The GENERIC cell, or "" when it holds the software's filler value."""
    value = clean(raw).rstrip(".")
    return "" if value.upper() in JUNK_GENERICS else value


def pack_count(raw) -> int:
    """'10' -> 10, "10'S" -> 10, '' -> 1."""
    digits = re.sub(r"\D", "", clean(raw))
    return int(digits) if digits else 1


def match(haystack: str, rules: list[tuple[str, tuple[str, ...]]]) -> str | None:
    for category, needles in rules:
        if any(needle in haystack for needle in needles):
            return category
    return None


def categorise(name: str, salt: str, manufacturer: str) -> str:
    """Salt first, then the product name, then the manufacturer's department."""
    return (
        (match(salt.upper(), SALT_RULES) if salt else None)
        or match(name.upper(), NAME_RULES)
        or match(manufacturer.upper(), MFG_RULES)
        or FALLBACK_CATEGORY
    )


def schedule_for(category: str, salt: str) -> str:
    if any(needle in salt.upper() for needle in H1_SALTS):
        return DrugSchedule.H1
    if category in RX_CATEGORIES:
        return DrugSchedule.H
    return DrugSchedule.OTC


def form_of(name: str, category: str = "") -> str:
    """The dose form in a product name, or "" when it does not have one.

    Only asked of things that are dispensed. A knee cap and a tooth paste are
    whole words away from "capsules" and "a paste", and a support bandage has no
    dose form to state at all.
    """
    if category in FORMLESS_CATEGORIES:
        return ""
    upper = name.upper()
    for needle, phrase in FORM_WORDS:
        if re.search(rf"\b{re.escape(needle)}\b", upper):
            return phrase
    return ""


def brand_of(name: str) -> str:
    """"Dolo 650 Tab" -> "Dolo". The first word is the brand on these lists."""
    return clean(name).split(" ")[0].title()[:255]


def description_for(
    name: str,
    manufacturer: str,
    category: str,
    salt: str,
    pack: int,
    schedule: str,
) -> str:
    """Prose assembled only from what the row actually states."""
    blurb = CATEGORY_BLURB.get(category, "a healthcare product")
    opening = f"{name} is {blurb}"
    if manufacturer:
        # Most makers are typed with a trailing "Ltd." or "Pvt.Ltd.,", which
        # would end the sentence in "Ltd...".
        opening += f" from {manufacturer.rstrip('.,')}"
    sentences = [opening + "."]

    if salt:
        sentences.append(f"Composition: {salt}.")

    form = form_of(name, category)
    if form and pack > 1:
        sentences.append(f"Supplied as {form}, {pack} per pack.")
    elif form:
        sentences.append(f"Supplied as {form}.")
    elif pack > 1:
        sentences.append(f"Pack of {pack}.")

    if schedule == DrugSchedule.H1:
        sentences.append(
            "Schedule H1 drug — sold only against a valid prescription, "
            "which the pharmacy is required to record.",
        )
    elif schedule == DrugSchedule.H:
        sentences.append("Prescription medicine — a valid prescription is required.")
    else:
        sentences.append("Available over the counter.")

    return " ".join(sentences)


class Command(BaseCommand):
    help = "Import a pharmacy price-list spreadsheet into the medicine catalogue."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the .xlsx price list")
        parser.add_argument("--sheet", help="Worksheet name (default: the first one)")
        parser.add_argument("--limit", type=int, help="Import at most N priced rows")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report what would change, writing nothing.",
        )

    def handle(self, *args, **options):
        import openpyxl  # a heavy import; only needed when this command runs

        path = Path(options["path"])
        if not path.exists():
            msg = f"Spreadsheet not found: {path}"
            raise CommandError(msg)

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet = workbook[options["sheet"]] if options["sheet"] else workbook.worksheets[0]

        parsed, stats = self.parse(sheet, options["limit"])
        self.report(parsed, stats)

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("dry run — nothing written"))
            return

        self.write(parsed)

    def parse(self, sheet, limit: int | None) -> tuple[list[dict], dict[str, int]]:
        """Rows -> import dicts, dropping banner, junk and unpriced rows.

        The header is found by looking for the PRODUCT cell rather than assumed
        at a fixed offset: the banner above it is the shop's name and address,
        and a different shop's export has a different number of those lines.
        """
        stats = {"rows": 0, "unpriced": 0, "junk": 0, "collisions": 0}
        parsed: list[dict] = []
        seen_keys: dict[tuple[str, str], int] = {}
        started = False

        for row in sheet.iter_rows(values_only=True):
            product = clean(row[0]) if row else ""
            if not started:
                started = product.upper() == "PRODUCT"
                continue
            if not product or product.upper() == "PRODUCT":
                continue

            stats["rows"] += 1

            if JUNK_NAME.match(product) or len(product) < 2 or not re.search(r"[A-Za-z]", product):
                stats["junk"] += 1
                continue

            mrp = money(row[5])
            if mrp <= 0:
                stats["unpriced"] += 1
                continue

            name = product.title()[:255]
            manufacturer = clean(row[2]).title()[:255]
            salt = salt_of(row[3])
            pack = pack_count(row[1])
            category = categorise(product, salt, clean(row[2]))
            schedule = schedule_for(category, salt)

            # Two rows for the same product and maker differing only by pack
            # would otherwise overwrite each other on the (name, manufacturer)
            # key, so the pack goes into the name of the second and later ones.
            key = (name, manufacturer)
            seen_keys[key] = seen_keys.get(key, 0) + 1
            if seen_keys[key] > 1:
                name = f"{name} ({pack})"[:255]
                stats["collisions"] += 1

            parsed.append(
                {
                    "name": name,
                    "manufacturer": manufacturer,
                    "brand": brand_of(product),
                    "content": salt or None,
                    "description": description_for(
                        name,
                        manufacturer,
                        category,
                        salt,
                        pack,
                        schedule,
                    ),
                    # No retail price column: the sheet carries MRP and the
                    # trade rate it buys at, so the catalogue sells at MRP.
                    # Pricing off TRADERATE would publish the shop's margin.
                    "price": mrp,
                    "mrp": mrp,
                    "stock": max(0, int(row[4] or 0)),
                    "schedule": schedule,
                    "category": category,
                }
            )

            if limit and len(parsed) >= limit:
                break

        return parsed, stats

    def report(self, parsed: list[dict], stats: dict[str, int]) -> None:
        self.stdout.write(
            f"rows read: {stats['rows']} · importable: {len(parsed)} · "
            f"skipped unpriced: {stats['unpriced']} · junk names: {stats['junk']} · "
            f"pack-disambiguated names: {stats['collisions']}",
        )

        counts: dict[str, int] = {}
        for item in parsed:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        self.stdout.write("categories:")
        for category, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            flag = "  (new)" if category in NEW_CATEGORIES else ""
            self.stdout.write(f"  {count:6d}  {category}{flag}")

        schedules: dict[str, int] = {}
        for item in parsed:
            schedules[item["schedule"]] = schedules.get(item["schedule"], 0) + 1
        self.stdout.write(f"schedules: {schedules}")
        self.stdout.write(f"out of stock but priced: {sum(1 for i in parsed if not i['stock'])}")

    @transaction.atomic
    def write(self, parsed: list[dict]) -> None:
        wanted = {item["category"] for item in parsed}
        categories = {}
        for name in sorted(wanted):
            categories[name], _ = Category.objects.get_or_create(
                name=name,
                defaults={
                    "is_active": True,
                    "description": NEW_CATEGORIES.get(name),
                },
            )

        created = updated = 0
        for index, item in enumerate(parsed):
            category = categories[item.pop("category")]
            medicine, was_created = Medicine.objects.update_or_create(
                name=item.pop("name"),
                manufacturer=item.pop("manufacturer"),
                # Medicine.save() derives is_prescription_required from schedule.
                defaults={**item, "is_active": True},
            )
            medicine.category.set([category])
            created += int(was_created)
            updated += int(not was_created)

            if (index + 1) % 2000 == 0:
                self.stdout.write(f"  ...{index + 1}/{len(parsed)}")

        self.stdout.write(
            self.style.SUCCESS(f"written. {created} new, {updated} updated"),
        )
        self.stdout.write(f"catalogue total: {Medicine.objects.count()}")
        self.stdout.write(f"prescription-only: {Medicine.objects.filter(is_prescription_required=True).count()}")
