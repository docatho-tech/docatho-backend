"""The receipt for a paid consultation.

A patient who paid for a video or clinic consultation had nothing to show for
it: the pharmacy side has had an invoice PDF since EP-03, but a consultation
fee — the larger of the two payments this product takes — produced no document
at all. Reimbursement, tax and a simple "what did I pay for" all need one.

Rendered on demand and streamed, never stored: a receipt is derived entirely
from the appointment, and keeping a PDF of a named patient's consultation on
the public media bucket would be one more exposed medical document.
"""

from __future__ import annotations

import io
from decimal import Decimal

from django.http import FileResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph
from reportlab.platypus import SimpleDocTemplate
from reportlab.platypus import Spacer
from reportlab.platypus import Table
from reportlab.platypus import TableStyle
from rest_framework import permissions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus


def _money(value) -> str:
    return f"Rs. {Decimal(value or 0):.2f}"


def receipt_number(appointment: Appointment) -> str:
    return f"RCPT-APPT-{appointment.pk:06d}"


def render_consultation_receipt_pdf(appointment: Appointment) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=receipt_number(appointment),
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Docatho", styles["Title"]))
    story.append(Paragraph("Consultation Receipt", styles["Heading2"]))
    story.append(Spacer(1, 8 * mm))

    txn = (
        appointment.payment_transactions.filter(succeeded=True)
        .order_by("-paid_at")
        .first()
    )
    doctor_name = getattr(
        getattr(appointment.doctor, "provider", None),
        "name",
        "",
    ) or str(appointment.doctor)

    meta_rows = [
        ["Receipt Number", receipt_number(appointment)],
        ["Consultation", appointment.scheduled_at.strftime("%Y-%m-%d %H:%M")],
        ["Mode", appointment.get_consultation_mode_display()],
        ["Patient", getattr(appointment.patient, "name", "") or str(appointment.patient)],
        ["Doctor", doctor_name],
        [
            "Payment",
            f"{appointment.payment_method or '-'} "
            f"({appointment.get_payment_status_display()})",
        ],
    ]
    if txn and txn.razorpay_payment_id:
        meta_rows.append(["Payment Reference", txn.razorpay_payment_id])
    if txn and txn.paid_at:
        meta_rows.append(["Paid At", txn.paid_at.strftime("%Y-%m-%d %H:%M")])

    meta_table = Table(meta_rows, colWidths=[45 * mm, 120 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ],
        ),
    )
    story.append(meta_table)
    story.append(Spacer(1, 8 * mm))

    # `fee` is the doctor's charge and `platform_fee` ours; they are paid to
    # different parties, so the receipt shows both rather than one total.
    fee = Decimal(appointment.fee or 0)
    platform_fee = Decimal(appointment.platform_fee or 0)
    amount_rows = [
        ["Description", "Amount"],
        ["Consultation fee", _money(fee)],
        ["Platform fee (incl. of GST)", _money(platform_fee)],
        ["Total paid", _money(fee + platform_fee)],
    ]
    amount_table = Table(amount_rows, colWidths=[135 * mm, 30 * mm])
    amount_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ],
        ),
    )
    story.append(amount_table)
    story.append(Spacer(1, 12 * mm))
    story.append(
        Paragraph(
            "This is a computer-generated receipt and does not require a "
            "signature.",
            styles["Italic"],
        ),
    )

    doc.build(story)
    return buffer.getvalue()


class ConsultationReceiptAPIView(APIView):
    """Download a paid consultation's receipt.

    Visible to the patient who paid, the doctor who was paid, and staff — the
    same three audiences as the appointment itself.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, appointment_id: int):
        appointment = (
            Appointment.objects.select_related(
                "patient",
                "doctor__provider",
            )
            .filter(pk=appointment_id)
            .first()
        )
        if appointment is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        is_patient = appointment.patient_id == request.user.pk
        is_doctor = (
            getattr(getattr(appointment.doctor, "provider", None), "user_id", None)
            == request.user.pk
        )
        if not (is_patient or is_doctor or request.user.is_staff):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # A receipt is evidence of a payment. There is nothing to receipt
        # until the money arrives, and issuing one for an unpaid or refunded
        # consultation would be a document claiming a payment that is not held.
        if appointment.payment_status != AppointmentPaymentStatus.PAID:
            return Response(
                {
                    "detail": "No receipt yet: this consultation has not been "
                    "paid for.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        pdf = render_consultation_receipt_pdf(appointment)
        return FileResponse(
            io.BytesIO(pdf),
            as_attachment=True,
            filename=f"{receipt_number(appointment)}.pdf",
            content_type="application/pdf",
        )
