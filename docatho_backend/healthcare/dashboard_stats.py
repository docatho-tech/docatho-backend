"""Date-window KPIs for the admin dashboard overview."""

from __future__ import annotations

from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import DiagnosticBooking
from docatho_backend.healthcare.models import DiagnosticBookingKind
from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.healthcare.models import DoctorProfile
from docatho_backend.healthcare.models import SupportTicket
from docatho_backend.healthcare.models import VerificationStatus
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import Prescription
from docatho_backend.users.models import User


def period_bounds(params):
    """Inclusive local-date window from `period` / `from` / `to` query params."""
    today = timezone.localdate()
    period = (params.get("period") or "today").strip().lower()
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = today
    elif period == "month":
        start = today.replace(day=1)
        end = today
    elif period == "all":
        start = today.replace(year=max(today.year - 20, 2000), month=1, day=1)
        end = today
    elif period == "custom":
        start = parse_date(params.get("from") or "") or today
        end = parse_date(params.get("to") or "") or today
        if end < start:
            start, end = end, start
    else:
        start = end = today

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end, time.max), tz)
    return period, start, end, start_dt, end_dt


SERIES_BUCKETS = 8


def _series(rows, start_dt, end_dt, buckets: int = SERIES_BUCKETS) -> list[float]:
    """Bucket `(when, weight)` pairs into equal slices of the window.

    Drives the sparkline on each figure card. Anything outside the window is
    clamped to the nearest end rather than dropped, so a row on the boundary
    still counts once — the totals beside the sparkline are computed from the
    same window and the two must agree.
    """
    span = (end_dt - start_dt).total_seconds() / buckets
    out = [0.0] * buckets
    if span <= 0:
        return out
    for when, weight in rows:
        if when is None:
            continue
        index = int((when - start_dt).total_seconds() // span)
        out[min(max(index, 0), buckets - 1)] += float(weight or 0)
    return out


def _change(current, previous):
    """Percentage change, or `None` when there is nothing to compare against.

    Same rule as `RevenueSummaryView`: growth from zero is a first sale, not
    "100% up", and a confident number there would be a lie on a card an admin
    reads at a glance.
    """
    if not previous:
        return None
    return round(((float(current) - float(previous)) / float(previous)) * 100, 1)


def _window_totals(start_dt, end_dt) -> dict:
    """The four headline figures for an arbitrary window."""
    orders = Order.objects.filter(placed_at__gte=start_dt, placed_at__lte=end_dt)
    appointments = Appointment.objects.filter(
        scheduled_at__gte=start_dt,
        scheduled_at__lte=end_dt,
    )
    bookings = DiagnosticBooking.objects.filter(
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    ).exclude(status=DiagnosticBookingStatus.CANCELLED)
    tests = bookings.filter(
        kind__in=[DiagnosticBookingKind.LAB, DiagnosticBookingKind.DIAGNOSTIC]
    )

    revenue = (
        (orders.filter(payment_status="paid").aggregate(t=Sum("total"))["t"] or 0)
        + (
            appointments.filter(
                payment_status=AppointmentPaymentStatus.PAID
            ).aggregate(t=Sum("fee"))["t"]
            or 0
        )
        + (bookings.aggregate(t=Sum("total_amount"))["t"] or 0)
    )

    return {
        "revenue": Decimal(revenue),
        "consultations": appointments.exclude(
            status__in=[AppointmentStatus.CANCELLED, AppointmentStatus.REJECTED]
        ).count(),
        "tests": tests.count(),
        "medicines": orders.exclude(status=Order.Status.CANCELLED).count(),
    }


def build_dashboard_stats(params) -> dict:
    period, start, end, start_dt, end_dt = period_bounds(params)
    patients = User.objects.filter(is_staff=False).exclude(provider__isnull=False)
    doctors = DoctorProfile.objects.all()
    paid_orders = Order.objects.filter(
        payment_status="paid",
        placed_at__gte=start_dt,
        placed_at__lte=end_dt,
    )
    appointments = Appointment.objects.filter(
        scheduled_at__gte=start_dt,
        scheduled_at__lte=end_dt,
    )
    bookings = DiagnosticBooking.objects.filter(
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    ).exclude(status=DiagnosticBookingStatus.CANCELLED)
    lab = bookings.filter(kind=DiagnosticBookingKind.LAB)
    imaging = bookings.filter(kind=DiagnosticBookingKind.DIAGNOSTIC)
    home = bookings.filter(kind=DiagnosticBookingKind.HOME_HEALTHCARE)

    order_revenue = paid_orders.aggregate(total=Sum("total"))["total"] or Decimal("0")
    consult_revenue = appointments.filter(
        payment_status=AppointmentPaymentStatus.PAID
    ).aggregate(total=Sum("fee"))["total"] or Decimal("0")
    lab_revenue = lab.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    diagnostic_revenue = imaging.aggregate(total=Sum("total_amount"))["total"] or Decimal(
        "0"
    )
    home_revenue = home.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    total_revenue = (
        order_revenue + consult_revenue + lab_revenue + diagnostic_revenue + home_revenue
    )

    customer_ids = set(paid_orders.values_list("user_id", flat=True)) | set(
        appointments.values_list("patient_id", flat=True)
    ) | set(bookings.values_list("patient_id", flat=True))

    consult_count = appointments.exclude(
        status__in=[AppointmentStatus.CANCELLED, AppointmentStatus.REJECTED]
    ).count()
    lab_count = lab.count()
    diagnostic_count = imaging.count()
    home_count = home.count()
    medicine_count = (
        Order.objects.filter(placed_at__gte=start_dt, placed_at__lte=end_dt)
        .exclude(status=Order.Status.CANCELLED)
        .count()
    )

    # The figure cards compare against the window immediately before this
    # one, of the same length, and draw a sparkline across the current one.
    span = end_dt - start_dt
    previous = _window_totals(start_dt - span, start_dt)
    current = {
        "revenue": total_revenue,
        "consultations": consult_count,
        "tests": lab_count + diagnostic_count,
        "medicines": medicine_count,
    }

    revenue_rows = (
        list(
            paid_orders.values_list("placed_at", "total"),
        )
        + list(
            appointments.filter(
                payment_status=AppointmentPaymentStatus.PAID
            ).values_list("scheduled_at", "fee"),
        )
        + list(bookings.values_list("created_at", "total_amount"))
    )

    return {
        "period": period,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "total_revenue_change": _change(current["revenue"], previous["revenue"]),
        "doctor_consultations_change": _change(
            current["consultations"], previous["consultations"]
        ),
        "tests_change": _change(current["tests"], previous["tests"]),
        "medicine_orders_change": _change(
            current["medicines"], previous["medicines"]
        ),
        "total_revenue_series": _series(revenue_rows, start_dt, end_dt),
        "doctor_consultations_series": _series(
            ((when, 1) for when in appointments.values_list("scheduled_at", flat=True)),
            start_dt,
            end_dt,
        ),
        "tests_series": _series(
            (
                (when, 1)
                for when in bookings.filter(
                    kind__in=[
                        DiagnosticBookingKind.LAB,
                        DiagnosticBookingKind.DIAGNOSTIC,
                    ]
                ).values_list("created_at", flat=True)
            ),
            start_dt,
            end_dt,
        ),
        "medicine_orders_series": _series(
            (
                (when, 1)
                for when in Order.objects.filter(
                    placed_at__gte=start_dt, placed_at__lte=end_dt
                )
                .exclude(status=Order.Status.CANCELLED)
                .values_list("placed_at", flat=True)
            ),
            start_dt,
            end_dt,
        ),
        "total_orders": consult_count
        + lab_count
        + diagnostic_count
        + home_count
        + medicine_count,
        "total_revenue": str(total_revenue),
        "total_customers": len(customer_ids)
        or patients.filter(date_joined__gte=start_dt, date_joined__lte=end_dt).count(),
        "doctor_consultations": consult_count,
        "lab_tests": lab_count,
        "lab_revenue": str(lab_revenue),
        "diagnostic_tests": diagnostic_count,
        "diagnostic_revenue": str(diagnostic_revenue),
        "home_healthcare": home_count,
        "home_healthcare_revenue": str(home_revenue),
        "medicine_orders": medicine_count,
        "patients_count": patients.count(),
        "doctors_count": doctors.count(),
        "pending_doctor_verifications": doctors.filter(
            verification_status=VerificationStatus.PENDING
        ).count(),
        "appointments_today": Appointment.objects.filter(
            scheduled_at__date=timezone.localdate()
        ).count(),
        "diagnostic_bookings_count": DiagnosticBooking.objects.count(),
        "diagnostic_bookings_requested": DiagnosticBooking.objects.filter(
            status="requested"
        ).count(),
        "lab_bookings_requested": DiagnosticBooking.objects.filter(
            kind=DiagnosticBookingKind.LAB, status="requested"
        ).count(),
        "imaging_bookings_requested": DiagnosticBooking.objects.filter(
            kind=DiagnosticBookingKind.DIAGNOSTIC, status="requested"
        ).count(),
        "home_care_bookings_requested": DiagnosticBooking.objects.filter(
            kind=DiagnosticBookingKind.HOME_HEALTHCARE, status="requested"
        ).count(),
        "open_support_tickets": SupportTicket.objects.filter(status="open").count(),
        "prescriptions_pending": Prescription.objects.filter(
            status=Prescription.Status.PENDING,
        ).count(),
        "appointments_pending_payment": Appointment.objects.filter(
            consultation_mode=ConsultationMode.ONLINE,
            payment_status=AppointmentPaymentStatus.PENDING,
            status__in=[AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED],
        ).count(),
        "pharma_orders_count": Order.objects.count(),
        "revenue_total": str(
            Order.objects.filter(payment_status="paid").aggregate(total=Sum("total"))[
                "total"
            ]
            or Decimal("0")
        ),
        "appointments_by_status": dict(
            Appointment.objects.values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count"),
        ),
    }
