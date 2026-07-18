"""Revenue, commission and medicine-sales analytics (EP-11 / EP-12).

All endpoints are admin-only except the provider payout summary, which a
provider may call to see their own earnings.
"""

import csv

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
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from docatho_backend.masters.permissions import IsAdmin
from docatho_backend.masters.permissions import is_provider

from .models import Order
from .models import OrderItem

DEC = DecimalField(max_digits=14, decimal_places=2)
ZERO = Value(0, output_field=DEC)

# An order counts as revenue once it is actually paid for:
#  - online orders: payment captured (payment_status = paid)
#  - COD orders: cash collected at delivery (status = delivered)
REVENUE_Q = Q(payment_status=Order.PaymentStatus.PAID) | Q(
    payment_method=Order.PaymentMethod.COD, status=Order.Status.DELIVERED,
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

        return Response(
            {
                "total_revenue": totals["total_revenue"],
                "total_commission": totals["total_commission"],
                "total_provider_earning": totals["total_provider_earning"],
                "total_orders": totals["total_orders"],
                "average_order_value": round(float(aov), 2),
                "period": period,
                "breakdown": breakdown,
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
                    Sum(F("quantity") * F("unit_price"), output_field=DEC), ZERO,
                ),
            )
            .order_by("-quantity_sold")[:10],
        )

        top_categories = list(
            items.values("medicine__category__id", "medicine__category__name")
            .annotate(
                quantity_sold=Coalesce(Sum("quantity"), Value(0)),
                revenue=Coalesce(
                    Sum(F("quantity") * F("unit_price"), output_field=DEC), ZERO,
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
