"""The "My Earnings" screen's three blocks: the week, the next payout, the lines.

A partner reconciling a bank transfer needs the subtraction spelled out — what
the patient paid, what the platform took, what is left — per line and not only
in a total. What a line *is* differs by persona (a consultation, a booking, an
order), so each persona answers the same three questions about its own rows.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import DiagnosticBooking
from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import Payout
from docatho_backend.orders.models import PayoutStatus
from docatho_backend.providers.partner_screens import persona_of

#: How many recent lines the screen shows before it needs a "see all".
LINE_LIMIT = 50

#: Payouts run weekly; the date shown is the next occurrence of this weekday.
PAYOUT_WEEKDAY = 5  # Saturday


def _money(value) -> str:
    return str(Decimal(str(value or 0)).quantize(Decimal("0.01")))


def earnings_lines(provider, since=None, until=None, limit=LINE_LIMIT) -> list[dict]:
    """The rows under "Earnings breakdown", newest first.

    ``since``/``until`` narrow the window and ``limit`` is what the screen can
    show — the CSV statement asks for a window and no limit, because a month
    truncated at fifty rows is not a statement anyone can reconcile against.
    """
    if provider is None:
        return []
    persona = persona_of(provider)
    if persona == "doctor":
        return _appointment_lines(provider, since, until, limit)
    if persona == "lab":
        return _booking_lines(provider, since, until, limit)
    return _order_lines(provider, since, until, limit)


def _window(queryset, field: str, since, until, limit):
    if since is not None:
        queryset = queryset.filter(**{f"{field}__gte": since})
    if until is not None:
        queryset = queryset.filter(**{f"{field}__lt": until})
    return queryset if limit is None else queryset[:limit]


def _appointment_lines(provider, since, until, limit) -> list[dict]:
    rows = _window(
        Appointment.objects.filter(
            doctor__provider=provider,
            status=AppointmentStatus.COMPLETED,
        )
        .select_related("patient")
        .order_by("-scheduled_at"),
        "scheduled_at", since, until, limit,
    )
    return [
        {
            "id": row.pk,
            # "Consultation" vs "Follow up": a repeat patient is a follow-up,
            # which is the distinction the design's rows draw.
            "kind": _appointment_kind(row),
            "name": row.patient.name,
            "amount": _money(row.fee),
            "platform_fee": _money(row.platform_fee),
            "net": _money((row.fee or 0) - (row.platform_fee or 0)),
            "mode": row.consultation_mode,
            "at": row.scheduled_at.isoformat(),
        }
        for row in rows
    ]


def _appointment_kind(appointment) -> str:
    seen_before = (
        Appointment.objects.filter(
            doctor=appointment.doctor,
            patient=appointment.patient,
            scheduled_at__lt=appointment.scheduled_at,
            status=AppointmentStatus.COMPLETED,
        ).exists()
    )
    return "Follow up" if seen_before else "Consultation"


def _booking_lines(provider, since, until, limit) -> list[dict]:
    rows = _window(
        DiagnosticBooking.objects.filter(
            center=provider,
            status__in=[
                DiagnosticBookingStatus.COMPLETED,
                DiagnosticBookingStatus.REPORT_GENERATED,
            ],
        )
        .select_related("patient")
        .order_by("-created_at"),
        "created_at", since, until, limit,
    )
    cut = Decimal(str(provider.commission_percent or 0)) / Decimal("100")
    lines = []
    for row in rows:
        gross = Decimal(str(row.total_amount or 0))
        fee = (gross * cut).quantize(Decimal("0.01"))
        lines.append(
            {
                "id": row.pk,
                "kind": "Test",
                "name": row.patient.name,
                "amount": _money(gross),
                "platform_fee": _money(fee),
                "net": _money(gross - fee),
                "mode": row.visit_type,
                "at": row.created_at.isoformat(),
            },
        )
    return lines


def _order_lines(provider, since, until, limit) -> list[dict]:
    rows = _window(
        Order.objects.filter(
            assigned_provider=provider,
            status=Order.Status.DELIVERED,
        )
        .order_by("-placed_at"),
        "placed_at", since, until, limit,
    )
    return [
        {
            "id": row.pk,
            "kind": "Order",
            "name": row.order_number,
            "amount": _money(row.total),
            "platform_fee": _money(row.commission_amount),
            "net": _money(row.provider_earning),
            "mode": row.payment_method,
            "at": row.placed_at.isoformat(),
        }
        for row in rows
    ]


def week_total(lines: list[dict]) -> str:
    """Net pay from the last seven days — the figure the screen leads with."""
    cutoff = timezone.now() - timedelta(days=7)
    total = sum(
        (Decimal(line["net"]) for line in lines if line["at"] >= cutoff.isoformat()),
        Decimal("0"),
    )
    return _money(total)


def amount_owed(provider, order_pending, lines=None) -> Decimal:
    """Everything earned and not yet transferred.

    ``order_pending`` — the caller's aggregate over unsettled *orders* — is the
    whole story for a pharmacy and none of it for a doctor, whose earnings are
    consultations. So a doctor's and a centre's owed figure is what their lines
    came to, less whatever has actually been paid out; there is no per-line paid
    marker, and the payout ledger is the only record of money that has moved.
    """
    if provider is None:
        return Decimal("0")
    if persona_of(provider) == "pharmacy":
        return Decimal(str(order_pending or 0))

    earned = sum(
        (Decimal(line["net"]) for line in (lines if lines is not None else earnings_lines(provider))),
        Decimal("0"),
    )
    settled = Payout.objects.filter(
        provider=provider,
        status=PayoutStatus.SETTLED,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return max(earned - Decimal(str(settled)), Decimal("0"))


def next_payout(provider, pending, lines=None) -> dict:
    """When the next transfer lands, for how much, and into which account.

    A pending :class:`Payout` row is the authority when one exists — ops has
    already committed to an amount and a date. Without one the screen still has
    to say something, so it shows what is currently owed against the next
    scheduled run.
    """
    bank_last4 = ""
    if provider and provider.bank_account_number:
        bank_last4 = str(provider.bank_account_number)[-4:]

    scheduled = (
        Payout.objects.filter(provider=provider, status=PayoutStatus.PENDING)
        .order_by("initiated_at")
        .first()
        if provider
        else None
    )
    amount = _money(scheduled.amount if scheduled else amount_owed(provider, pending, lines))

    today = timezone.localdate()
    ahead = (PAYOUT_WEEKDAY - today.weekday()) % 7 or 7
    return {
        "amount": amount,
        "credited_on": (today + timedelta(days=ahead)).isoformat(),
        # "Scheduled" only once there is both money to send and somewhere to
        # send it — a payout with no account is the thing the screen's amber
        # notice exists to get fixed.
        "status": "scheduled" if bank_last4 and Decimal(amount) > 0 else "unscheduled",
        "bank_name": (provider.bank_ifsc or "")[:4] if provider else "",
        "bank_last4": bank_last4,
        "has_bank": bool(bank_last4),
    }
