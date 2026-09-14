"""Revenue, commission and medicine-sales analytics (EP-11 / EP-12).

All endpoints are admin-only except the provider payout summary, which a
provider may call to see their own earnings.
"""

import csv
from datetime import date
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count
from django.db.models import DecimalField
from django.db.models import F
from django.db.models import Q
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncDate
from django.db.models.functions import TruncMonth
from django.db.models.functions import TruncWeek
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from docatho_backend.masters.permissions import IsAdmin
from docatho_backend.masters.permissions import is_provider

from .models import Order
from .models import OrderItem

DEC = DecimalField(max_digits=14, decimal_places=2)
ZERO = Value(0, output_field=DEC)
#: The plain number, for arithmetic outside a queryset. `ZERO` is a query
#: expression and raises the moment it is subtracted from a Decimal.
ZERO_DECIMAL = Decimal("0")

# An order counts as revenue once it is actually paid for:
#  - online orders: payment captured (payment_status = paid)
#  - COD orders: cash collected at delivery (status = delivered)
REVENUE_Q = Q(payment_status=Order.PaymentStatus.PAID) | Q(
    payment_method=Order.PaymentMethod.COD,
    status=Order.Status.DELIVERED,
)

_TRUNC = {"day": TruncDate, "week": TruncWeek, "month": TruncMonth}


def _revenue_orders():
    return Order.objects.filter(REVENUE_Q)


def _date_range(request):
    """Optional ?start=YYYY-MM-DD&end=YYYY-MM-DD filter."""
    start = request.query_params.get("start")
    end = request.query_params.get("end")
    flt = {}
    if start:
        flt["placed_at__date__gte"] = start
    if end:
        flt["placed_at__date__lte"] = end
    return flt


def _previous_range(request) -> dict | None:
    """
    The window of equal length immediately before the requested one.

    Returns ``None`` when no range was asked for: "all time" has no previous
    period, and reporting a change against a window that does not exist is
    how a dashboard ends up showing a confident percentage that means nothing.
    """
    start = request.query_params.get("start")
    if not start:
        return None
    try:
        start_date = date.fromisoformat(start)
        end_date = (
            date.fromisoformat(request.query_params["end"])
            if request.query_params.get("end")
            else timezone.localdate()
        )
    except ValueError:
        return None
    span = (end_date - start_date).days or 1
    return {
        "placed_at__date__gte": start_date - timedelta(days=span),
        "placed_at__date__lt": start_date,
    }


def _percent_change(current, previous) -> float | None:
    """
    Percent movement, or ``None`` when the comparison is meaningless.

    Growth from zero is not "100% up" — it is a first sale, and dividing by
    zero to say otherwise is the arithmetic every dashboard gets wrong.
    """
    if previous in (None, 0) or Decimal(previous) == 0:
        return None
    change = (Decimal(current) - Decimal(previous)) / Decimal(previous) * 100
    return round(float(change), 1)


class RevenueSummaryView(APIView):
    """GET /api/analytics/revenue/ — totals + per-period breakdown (admin)."""

    permission_classes = (IsAdmin,)

    def get(self, request):
        qs = _revenue_orders().filter(**_date_range(request))
        totals = qs.aggregate(
            total_revenue=Coalesce(Sum("total"), ZERO),
            total_commission=Coalesce(Sum("commission_amount"), ZERO),
            total_provider_earning=Coalesce(Sum("provider_earning"), ZERO),
            total_orders=Count("id"),
        )
        aov = (
            (totals["total_revenue"] / totals["total_orders"])
            if totals["total_orders"]
            else 0
        )

        period = request.query_params.get("period", "day")
        trunc = _TRUNC.get(period, TruncDate)
        breakdown = list(
            qs.annotate(bucket=trunc("placed_at"))
            .values("bucket")
            .annotate(
                revenue=Coalesce(Sum("total"), ZERO),
                commission=Coalesce(Sum("commission_amount"), ZERO),
                orders=Count("id"),
            )
            .order_by("bucket"),
        )

        # How each headline moved against the window before this one. Absent
        # when no range was requested, so the client draws no trend rather
        # than an invented one.
        deltas = {}
        previous_range = _previous_range(request)
        if previous_range:
            previous = _revenue_orders().filter(**previous_range).aggregate(
                total_revenue=Coalesce(Sum("total"), ZERO),
                total_commission=Coalesce(Sum("commission_amount"), ZERO),
                total_provider_earning=Coalesce(Sum("provider_earning"), ZERO),
                total_orders=Count("id"),
            )
            deltas = {
                key: _percent_change(totals[key], previous[key]) for key in totals
            }

        return Response(
            {
                "total_revenue": totals["total_revenue"],
                "total_commission": totals["total_commission"],
                "total_provider_earning": totals["total_provider_earning"],
                "total_orders": totals["total_orders"],
                "average_order_value": round(float(aov), 2),
                "period": period,
                "breakdown": breakdown,
                "deltas": deltas,
            },
        )


class RevenueExportView(APIView):
    """GET /api/analytics/revenue/export/ — CSV of revenue orders (admin)."""

    permission_classes = (IsAdmin,)

    def get(self, request):
        qs = _revenue_orders().filter(**_date_range(request)).order_by("-placed_at")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="revenue.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "order_number",
                "placed_at",
                "status",
                "payment_method",
                "total",
                "commission_amount",
                "provider_earning",
            ],
        )
        for o in qs.iterator():
            writer.writerow(
                [
                    o.order_number,
                    o.placed_at.isoformat(),
                    o.status,
                    o.payment_method,
                    o.total,
                    o.commission_amount,
                    o.provider_earning,
                ],
            )
        return response


class SalesAnalyticsView(APIView):
    """GET /api/analytics/sales/ — top medicines & categories (admin, EP-12)."""

    permission_classes = (IsAdmin,)

    def get(self, request):
        item_flt = {f"order__{k}": v for k, v in _date_range(request).items()}
        items = OrderItem.objects.filter(order__in=_revenue_orders()).filter(**item_flt)

        top_products = list(
            items.values("medicine_id", "medicine__name")
            .annotate(
                quantity_sold=Coalesce(Sum("quantity"), Value(0)),
                revenue=Coalesce(
                    Sum(F("quantity") * F("unit_price"), output_field=DEC),
                    ZERO,
                ),
            )
            .order_by("-quantity_sold")[:10],
        )

        top_categories = list(
            items.values("medicine__category__id", "medicine__category__name")
            .annotate(
                quantity_sold=Coalesce(Sum("quantity"), Value(0)),
                revenue=Coalesce(
                    Sum(F("quantity") * F("unit_price"), output_field=DEC),
                    ZERO,
                ),
            )
            .exclude(medicine__category__isnull=True)
            .order_by("-revenue")[:10],
        )

        return Response(
            {"top_products": top_products, "top_categories": top_categories},
        )


class ProviderPayoutSummaryView(APIView):
    """GET /api/analytics/payouts/ — provider payout summary (EP-07/EP-11).

    Admins see all providers; a provider sees only their own totals.
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        user = request.user
        if not (user.is_staff or is_provider(user)):
            return Response({"detail": "Not permitted."}, status=403)

        qs = _revenue_orders().filter(assigned_provider__isnull=False)
        if not user.is_staff:
            qs = qs.filter(assigned_provider__user=user)

        rows = list(
            qs.values("assigned_provider_id", "assigned_provider__name")
            .annotate(
                orders=Count("id"),
                gross=Coalesce(Sum("total"), ZERO),
                commission=Coalesce(Sum("commission_amount"), ZERO),
                payout=Coalesce(Sum("provider_earning"), ZERO),
            )
            .order_by("-payout"),
        )
        return Response({"providers": rows})


class SettlementSummaryView(APIView):
    """
    GET /api/analytics/settlements/ — what each partner has earned and is owed.

    The existing payout summary counts medicine orders only, so a lab or a
    doctor settled at ₹0 no matter how much they had billed. This one asks
    ``_provider_sale_summary`` for the figures, which already knows that a
    doctor earns through appointments, a centre through bookings and a
    chemist through orders — and subtracts the payouts already sent, because
    "earned" and "still owed" are different questions.

    # ponytail: one summary query per provider. Fine at this catalogue size;
    # if the partner list grows past a few hundred, fold the three per-type
    # aggregates into one grouped query per type instead.
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        from docatho_backend.orders.models import Payout
        from docatho_backend.orders.models import PayoutStatus
        from docatho_backend.providers.models import Provider
        from docatho_backend.providers.serializers import _provider_sale_summary

        if not request.user.is_staff:
            return Response({"detail": "Not permitted."}, status=403)

        providers = Provider.objects.select_related("user").order_by("name")
        provider_type = request.query_params.get("provider_type")
        if provider_type:
            providers = providers.filter(provider_type=provider_type)

        paid_by_provider = {
            row["provider_id"]: row["total"]
            for row in Payout.objects.filter(status=PayoutStatus.SETTLED)
            .values("provider_id")
            .annotate(total=Coalesce(Sum("amount"), ZERO))
        }

        rows = []
        for provider in providers:
            summary = _provider_sale_summary(provider)
            revenue = Decimal(summary["revenue_total"])
            commission = Decimal(summary["commission_total"])
            settled = Decimal(paid_by_provider.get(provider.id, ZERO_DECIMAL))
            rows.append(
                {
                    "provider_id": provider.id,
                    "name": provider.name,
                    "provider_type": provider.provider_type,
                    "logo_url": provider.logo_url,
                    "city": provider.city,
                    "order_count": summary["order_count"],
                    "revenue_total": str(revenue),
                    "commission_percent": str(provider.commission_percent),
                    "commission_total": str(commission),
                    "settled_total": str(settled),
                    "net_payable": str(revenue - commission - settled),
                },
            )
        return Response({"settlements": rows})
