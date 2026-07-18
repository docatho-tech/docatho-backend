"""Invoice PDF generation (EP-03 / EP-10).

Pure-Python PDF via reportlab (no system libraries required). The rendered PDF
is stored on ``Invoice.pdf`` through the default storage backend.
"""

from __future__ import annotations

import io
from decimal import Decimal

from django.core.files.base import ContentFile
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph
from reportlab.platypus import SimpleDocTemplate
from reportlab.platypus import Spacer
from reportlab.platypus import Table
from reportlab.platypus import TableStyle

from .models import Invoice
from .models import Order


def _money(value: Decimal) -> str:
    return f"Rs. {Decimal(value or 0):.2f}"


def render_invoice_pdf(order: Order) -> bytes:
    """Render an order into invoice PDF bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"Invoice {order.order_number}",
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Docatho Pharmacy", styles["Title"]))
    story.append(Paragraph("Tax Invoice", styles["Heading2"]))
    story.append(Spacer(1, 8 * mm))

    address = order.address
    address_line = (
        f"{address.address_line1}, {address.city}, {address.state} "
        f"{address.postal_code}"
        if address
        else "-"
    )
    meta_rows = [
        ["Order Number", order.order_number],
        ["Placed At", order.placed_at.strftime("%Y-%m-%d %H:%M")],
        ["Customer", getattr(order.user, "name", "") or str(order.user)],
        ["Delivery Address", address_line],
        ["Payment", f"{order.get_payment_method_display()} ({order.payment_status})"],
    ]
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

    item_rows = [["#", "Medicine", "Qty", "Unit", "Line Total"]]
    for idx, it in enumerate(order.items.select_related("medicine").all(), start=1):
        item_rows.append(
            [
                str(idx),
                it.medicine.name,
                str(it.quantity),
                _money(it.unit_price),
                _money(it.line_total),
            ],
        )
    items_table = Table(item_rows, colWidths=[10 * mm, 85 * mm, 15 * mm, 25 * mm, 30 * mm])
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ],
        ),
    )
    story.append(items_table)
    story.append(Spacer(1, 6 * mm))

    totals_rows = [
        ["Subtotal", _money(order.subtotal)],
        ["Delivery Fee", _money(order.delivery_fee)],
        ["Discount", f"- {_money(order.discount_amount)}"],
        ["Total", _money(order.total)],
    ]
    totals_table = Table(totals_rows, colWidths=[135 * mm, 30 * mm])
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
            ],
        ),
    )
    story.append(totals_table)
    story.append(Spacer(1, 12 * mm))
    story.append(
        Paragraph(
            "This is a computer-generated invoice and does not require a signature.",
            styles["Italic"],
        ),
    )

    doc.build(story)
    return buffer.getvalue()


def get_or_create_invoice(order: Order) -> Invoice:
    """Return the order's invoice, generating the PDF on first request."""
    invoice, _created = Invoice.objects.get_or_create(
        order=order,
        defaults={"invoice_number": f"INV{order.order_number}"},
    )
    if not invoice.pdf:
        pdf_bytes = render_invoice_pdf(order)
        invoice.pdf.save(
            f"{invoice.invoice_number}.pdf",
            ContentFile(pdf_bytes),
            save=True,
        )
    return invoice
