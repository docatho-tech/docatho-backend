"""Rendering an appointment's e-prescription to PDF.

The layout follows the format the clinical team drew: a Docatho E-Consultation
header, patient and doctor blocks side by side, vitals, then complaints,
allergy/history, diagnosis and advice, the medications table, special
instructions and follow-up, and a signature box.

reportlab rather than an HTML-to-PDF library, because that is already how the
order invoice is produced and it needs no system packages.
"""

from __future__ import annotations

import io

from django.core.files.base import ContentFile
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph
from reportlab.platypus import SimpleDocTemplate
from reportlab.platypus import Spacer
from reportlab.platypus import Table
from reportlab.platypus import TableStyle

BRAND = colors.HexColor("#204BB6")
HAIRLINE = colors.HexColor("#C9C9C9")

MEDICATION_COLUMNS = ["#", "Type", "Drug name & composition", "Dose", "Duration"]


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "RxTitle", parent=base["Title"], fontSize=20, textColor=BRAND,
            spaceAfter=0,
        ),
        "label": ParagraphStyle(
            "RxLabel", parent=base["Normal"], fontSize=8.5,
            textColor=colors.HexColor("#6B6B6B"),
        ),
        "body": ParagraphStyle(
            "RxBody", parent=base["Normal"], fontSize=10, leading=14,
        ),
        "section": ParagraphStyle(
            "RxSection", parent=base["Normal"], fontSize=10,
            fontName="Helvetica-Bold", textColor=BRAND,
        ),
        "fine": ParagraphStyle(
            "RxFine", parent=base["Normal"], fontSize=7.5, leading=10,
            textColor=colors.HexColor("#6B6B6B"),
        ),
    }


def _boxed(rows, widths, styles) -> Table:
    table = Table(rows, colWidths=widths)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.6, HAIRLINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, HAIRLINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ],
        ),
    )
    return table


def _pairs(items: list[tuple[str, str]], styles) -> list:
    """"Label: value" lines, skipping the ones nobody filled in."""
    return [
        Paragraph(f"<b>{label}:</b> {value}", styles["body"])
        for label, value in items
        if str(value or "").strip()
    ]


def render_prescription_pdf(prescription) -> bytes:
    appointment = prescription.appointment
    patient = appointment.patient
    doctor = appointment.doctor
    provider = doctor.provider
    styles = _styles()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=f"Prescription — appointment {appointment.pk}",
    )
    width = doc.width
    story: list = [
        Paragraph("DOCATHO", styles["title"]),
        Paragraph("E-Consultation", styles["section"]),
        Spacer(1, 4 * mm),
    ]

    issued = timezone.localtime(prescription.created_at or timezone.now())
    story.append(
        Paragraph(
            f"Date &amp; Time: {issued.strftime('%d %b %Y, %I:%M %p')}",
            styles["label"],
        ),
    )
    story.append(Spacer(1, 3 * mm))

    # -- who it is for, and who wrote it --------------------------------
    qualifications = doctor.qualifications or []
    degree = ", ".join(str(item) for item in qualifications) if qualifications else ""
    patient_block = [Paragraph("Patient details", styles["section"]), *_pairs(
        [
            ("Name", getattr(patient, "name", "") or str(patient)),
            ("Age / Sex", _age_sex(patient)),
            ("City", getattr(patient, "city", "") or provider.city),
            ("Appointment ID", f"#{appointment.pk}"),
        ],
        styles,
    )]
    doctor_block = [Paragraph("Doctor details", styles["section"]), *_pairs(
        [
            ("Name", provider.name),
            ("Degree", degree),
            ("Med Reg No", doctor.medical_reg_no),
            ("Mob No", getattr(provider.user, "phone", "")),
            ("Address", _clinic_address(doctor, provider)),
        ],
        styles,
    )]
    story.append(_boxed([[patient_block, doctor_block]], [width / 2] * 2, styles))
    story.append(Spacer(1, 4 * mm))

    vitals = _pairs(
        [
            ("BP", prescription.bp),
            ("Pulse", prescription.pulse),
            ("Weight", prescription.weight),
            ("Height", prescription.height),
        ],
        styles,
    )
    if vitals:
        story.append(
            _boxed(
                [[Paragraph("Vitals", styles["section"]), vitals]],
                [width * 0.25, width * 0.75],
                styles,
            ),
        )
        story.append(Spacer(1, 4 * mm))

    # -- the consultation itself ----------------------------------------
    sections = [
        ("Complaints", prescription.complaints),
        ("Allergy / History", prescription.allergies),
        ("Diagnosis", prescription.diagnosis),
        ("Advice", prescription.advice),
    ]
    filled = [
        [Paragraph(label, styles["section"]), Paragraph(_lines(value), styles["body"])]
        for label, value in sections
        if str(value or "").strip()
    ]
    if filled:
        story.append(_boxed(filled, [width * 0.25, width * 0.75], styles))
        story.append(Spacer(1, 4 * mm))

    medications = _medication_rows(prescription.medications, styles)
    if medications:
        story.append(Paragraph("Medications", styles["section"]))
        story.append(Spacer(1, 2 * mm))
        table = Table(
            [MEDICATION_COLUMNS, *medications],
            colWidths=[
                width * 0.06, width * 0.14, width * 0.44, width * 0.18, width * 0.18,
            ],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.4, HAIRLINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ],
            ),
        )
        story.append(table)
        story.append(Spacer(1, 4 * mm))

    for label, value in (
        ("Special instructions", prescription.instructions),
        ("Follow up", prescription.follow_up),
    ):
        if str(value or "").strip():
            story.append(Paragraph(f"<b>{label}:</b> {_lines(value)}", styles["body"]))
            story.append(Spacer(1, 2 * mm))

    story.append(Spacer(1, 10 * mm))
    signature = Table(
        [[
            Paragraph(
                "This prescription is issued after a teleconsultation and is valid "
                "as per the Telemedicine Practice Guidelines. It is not a substitute "
                "for emergency care.",
                styles["fine"],
            ),
            Paragraph(
                f"<b>{provider.name}</b><br/>Signature &amp; seal",
                styles["body"],
            ),
        ]],
        colWidths=[width * 0.6, width * 0.4],
    )
    signature.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LINEABOVE", (1, 0), (1, 0), 0.6, colors.black),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ],
        ),
    )
    story.append(signature)

    doc.build(story)
    return buffer.getvalue()


def _age_sex(patient) -> str:
    age = getattr(patient, "age", None)
    gender = getattr(patient, "gender", "") or ""
    parts = [f"{age} y" if age else "", gender.title()]
    return " / ".join(part for part in parts if part)


def _clinic_address(doctor, provider) -> str:
    parts = [
        doctor.clinic_name,
        doctor.clinic_address,
        doctor.clinic_city or provider.city,
    ]
    return ", ".join(part for part in parts if part)


def _lines(value: str) -> str:
    """Free text keeps the doctor's own line breaks; reportlab wants <br/>."""
    return str(value).replace("\n", "<br/>")


def _medication_rows(medications, styles) -> list[list]:
    rows = []
    for index, item in enumerate(medications or [], start=1):
        if not isinstance(item, dict):
            continue
        drug = str(item.get("drug") or "").strip()
        if not drug:
            continue
        rows.append(
            [
                str(index),
                str(item.get("type") or ""),
                Paragraph(drug, styles["body"]),
                str(item.get("dose") or ""),
                str(item.get("duration") or ""),
            ],
        )
    return rows


def store_prescription_pdf(prescription) -> None:
    """Render and attach the PDF, replacing whatever was there before.

    Called on every save of a generated prescription: the doctor correcting a
    dose has to change the document the patient downloads, not only the row.
    """
    prescription.pdf.save(
        f"prescription-{prescription.appointment_id}.pdf",
        ContentFile(render_prescription_pdf(prescription)),
        save=True,
    )
