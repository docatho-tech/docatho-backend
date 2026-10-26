"""The partner app's Insights and Catalog screens.

Both are per-persona views of data the rest of the API already holds — a
doctor's insights count consultations, a lab's count tests, a pharmacy's count
orders — so the shape is declared once here and the queryset picked per
persona, rather than three endpoints that drift apart.
"""

from __future__ import annotations

from datetime import date
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import DiagnosticBooking
from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.healthcare.models import DiagnosticTest
from docatho_backend.masters.permissions import IsProvider
from docatho_backend.medicines.models import Medicine
from docatho_backend.medicines.models import ProviderMedicine
from docatho_backend.orders.models import Order
from docatho_backend.providers.enums import ProviderType
from docatho_backend.providers.models import Provider

#: The three line colours the design uses, in the order the cards appear.
CHART_BLUE = "#204BB6"
CHART_GREEN = "#509E2D"
CHART_ORANGE = "#E36940"

#: Windows the Insights screen's picker offers, as day counts.
PERIODS = {"week": 7, "month": 30, "quarter": 90}

#: A custom range is still a day per point and a label per day, so it is capped
#: where the chart stops being readable rather than where the query gets slow.
MAX_RANGE_DAYS = 92


def _provider_for(user) -> Provider | None:
    return Provider.objects.filter(user=user).first()


def persona_of(provider: Provider | None) -> str:
    """doctor / lab / pharmacy — the three the partner app is built for.

    Anything unrecognised is a pharmacy, matching `useProviderType` in the app:
    an unknown partner type showing an empty screen is worse than one showing
    the order queue it probably has.
    """
    kind = getattr(provider, "provider_type", "")
    if kind == ProviderType.DOCTOR.value:
        return "doctor"
    if kind in (ProviderType.LAB.value, ProviderType.DIAGNOSTIC_CENTER.value):
        return "lab"
    return "pharmacy"


def _day_starts(days: int, last=None) -> list:
    """The local midnights of the window, oldest first."""
    end = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    if last is not None:
        end = end.replace(year=last.year, month=last.month, day=last.day)
    return [end - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _parse_date(value: str | None):
    """`YYYY-MM-DD` or nothing — anything else is treated as not asked for."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def resolve_window(params) -> tuple[list, str]:
    """The days a screen is asking about, oldest first, and what to call them.

    Two ways to ask, because the period sheet offers both: a named preset, or a
    ``start``/``end`` pair the partner picked. Insights and the CSV statement
    resolve it the same way so "Export" sends exactly the window on screen.
    """
    start = _parse_date(params.get("start"))
    end = _parse_date(params.get("end"))
    if start and end and start <= end:
        return _day_starts(min((end - start).days + 1, MAX_RANGE_DAYS), end), "custom"

    period = params.get("period", "week")
    if period not in PERIODS:
        period = "week"
    return _day_starts(PERIODS[period]), period


def _bucket(rows, field: str, days: list, value) -> list:
    """Sum `value(row)` into one bucket per day in `days`.

    Done in Python rather than a `TruncDate` aggregate because the buckets must
    include the days with nothing in them — a week with no Tuesday row is a
    Tuesday at zero, not a six-point chart.
    """
    totals = [Decimal("0") for _ in days]
    starts = [day.timestamp() for day in days]
    end = days[-1].timestamp() + 86400
    for row in rows:
        when = timezone.localtime(getattr(row, field))
        stamp = when.timestamp()
        if stamp < starts[0] or stamp >= end:
            continue
        index = int((stamp - starts[0]) // 86400)
        if 0 <= index < len(totals):
            totals[index] += Decimal(str(value(row) or 0))
    return [float(total) for total in totals]


def _labels(days: list) -> list[str]:
    """A weekday per point for a week; about seven dates for anything longer.

    Ninety day-names under a ninety-point chart is a grey smear. The blanks are
    kept rather than dropped so each surviving label still sits over its own
    point — the app spreads the labels across the chart's width.
    """
    if len(days) <= 7:
        return [day.strftime("%a") for day in days]
    step = max(1, round(len(days) / 7))
    return [
        day.strftime("%-d %b") if index % step == 0 else ""
        for index, day in enumerate(days)
    ]


def _metric(key, label, colour, fmt, series, previous) -> dict:
    return {
        "key": key,
        "label": label,
        "color": colour,
        # "money" prints with a thousands separator and two decimals; "count"
        # is a bare integer. The app cannot infer which from the number alone —
        # 234 consultations and ₹234.00 are the same float.
        "format": fmt,
        "value": round(sum(series), 2),
        "series": series,
        "previous": previous,
    }


def _average_metric(key, label, colour, totals, counts, previous_totals, previous_counts):
    """Avg. order value — a ratio per day, not a sum, so it needs its own shape."""
    def ratio(amounts, quantities):
        return [
            round(amount / quantity, 2) if quantity else 0.0
            for amount, quantity in zip(amounts, quantities, strict=True)
        ]

    series = ratio(totals, counts)
    metric = _metric(key, label, colour, "money", series, ratio(previous_totals, previous_counts))
    # The headline for an average is the period's average, not the sum of the
    # daily averages — adding seven averages answers no question anyone asked.
    grand_total, grand_count = sum(totals), sum(counts)
    metric["value"] = round(grand_total / grand_count, 2) if grand_count else 0.0
    return metric


class ProviderInsightsAPIView(APIView):
    """`GET /api/providers/insights/?period=week|month` — the Insights screen.

    Each metric carries its own series *and* the previous window's, because
    every chart in the design draws the comparison as a dotted line behind the
    solid one. Sending them apart would make the screen ask twice for one
    picture.
    """

    permission_classes = [IsProvider]

    def get(self, request):
        provider = _provider_for(request.user)
        days, period = resolve_window(request.query_params)
        previous_days = [day - timedelta(days=len(days)) for day in days]
        window_start = previous_days[0]

        persona = persona_of(provider)
        builder = {
            "doctor": self._doctor_metrics,
            "lab": self._lab_metrics,
            "pharmacy": self._pharmacy_metrics,
        }[persona]

        return Response(
            {
                "period": period,
                # Echoed so the picker can label a custom range with the dates
                # the server actually used after the cap.
                "start": days[0].date().isoformat(),
                "end": days[-1].date().isoformat(),
                "persona": persona,
                "labels": _labels(days),
                "metrics": builder(provider, days, previous_days, window_start),
            },
        )

    def _doctor_metrics(self, provider, days, previous_days, window_start):
        rows = list(
            Appointment.objects.filter(
                doctor__provider=provider,
                status=AppointmentStatus.COMPLETED,
                scheduled_at__gte=window_start,
            ).only("scheduled_at", "fee", "platform_fee"),
        )
        net = lambda row: (row.fee or 0) - (row.platform_fee or 0)  # noqa: E731
        return [
            _metric(
                "net_earnings", "Net Earnings", CHART_BLUE, "money",
                _bucket(rows, "scheduled_at", days, net),
                _bucket(rows, "scheduled_at", previous_days, net),
            ),
            _metric(
                "consultations", "Total Consultations", CHART_GREEN, "count",
                _bucket(rows, "scheduled_at", days, lambda row: 1),
                _bucket(rows, "scheduled_at", previous_days, lambda row: 1),
            ),
        ]

    def _lab_metrics(self, provider, days, previous_days, window_start):
        rows = list(
            DiagnosticBooking.objects.filter(
                center=provider,
                status__in=[
                    DiagnosticBookingStatus.COMPLETED,
                    DiagnosticBookingStatus.REPORT_GENERATED,
                ],
                created_at__gte=window_start,
            ).only("created_at", "total_amount"),
        )
        amount = lambda row: row.total_amount  # noqa: E731
        one = lambda row: 1  # noqa: E731
        totals = _bucket(rows, "created_at", days, amount)
        counts = _bucket(rows, "created_at", days, one)
        previous_totals = _bucket(rows, "created_at", previous_days, amount)
        previous_counts = _bucket(rows, "created_at", previous_days, one)
        return [
            _metric("net_earnings", "Net Earnings", CHART_BLUE, "money", totals, previous_totals),
            _metric("tests_booked", "Tests Booked", CHART_GREEN, "count", counts, previous_counts),
            _average_metric(
                "avg_order_value", "Avg, Order Value", CHART_ORANGE,
                totals, counts, previous_totals, previous_counts,
            ),
        ]

    def _pharmacy_metrics(self, provider, days, previous_days, window_start):
        rows = list(
            Order.objects.filter(
                assigned_provider=provider,
                status=Order.Status.DELIVERED,
                placed_at__gte=window_start,
            ).only("placed_at", "total", "provider_earning"),
        )
        earning = lambda row: row.provider_earning  # noqa: E731
        total = lambda row: row.total  # noqa: E731
        one = lambda row: 1  # noqa: E731
        sales = _bucket(rows, "placed_at", days, earning)
        totals = _bucket(rows, "placed_at", days, total)
        counts = _bucket(rows, "placed_at", days, one)
        previous_totals = _bucket(rows, "placed_at", previous_days, total)
        previous_counts = _bucket(rows, "placed_at", previous_days, one)
        return [
            _metric(
                "net_sales", "Net Sales", CHART_BLUE, "money", sales,
                _bucket(rows, "placed_at", previous_days, earning),
            ),
            _metric("orders_delivered", "Orders Delivered", CHART_GREEN, "count", counts, previous_counts),
            _average_metric(
                "avg_order_value", "Avg, Order Value", CHART_ORANGE,
                totals, counts, previous_totals, previous_counts,
            ),
        ]


class ProviderCatalogAPIView(APIView):
    """`GET/PATCH /api/providers/catalog/` — the Medicines and Tests catalogues.

    One endpoint, because they are one screen: the same card, the same filter
    strip, the same availability switch, over a different noun. `kind` tells the
    app which it is looking at so it can title itself.

    PATCH takes only the switches — ``{"items": [{"id": 4, "is_available": false}]}``
    — which is what the "Save changes" button sends. Prices are set by the
    platform, not the partner, so they are read-only here.
    """

    permission_classes = [IsProvider]

    def get(self, request):
        provider = _provider_for(request.user)
        if persona_of(provider) == "lab":
            return Response(self._tests(provider))
        return Response(self._medicines(provider))

    def patch(self, request):
        provider = _provider_for(request.user)
        items = request.data.get("items")
        if not isinstance(items, list):
            return Response(
                {"detail": "items must be a list of {id, is_available}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        wanted = {}
        for item in items:
            try:
                wanted[int(item["id"])] = bool(item["is_available"])
            except (KeyError, TypeError, ValueError):
                return Response(
                    {"detail": "Each item needs an id and an is_available."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if persona_of(provider) == "lab":
            updated = self._save_tests(provider, wanted)
            return Response(self._tests(provider) | {"updated": updated})
        updated = self._save_medicines(provider, wanted)
        return Response(self._medicines(provider) | {"updated": updated})

    # -- tests ------------------------------------------------------------

    def _lab_tests(self, provider):
        # The centre's own rows, plus the shared catalogue every centre can run.
        return DiagnosticTest.objects.filter(
            Q(provider=provider) | Q(provider__isnull=True),
        ).select_related("category")

    def _tests(self, provider) -> dict:
        tests = self._lab_tests(provider)
        return {
            "kind": "tests",
            "title": "Tests Catalog",
            "search_placeholder": "Search for tests",
            "categories": sorted(
                {test.category.name for test in tests if test.category},
            ),
            "items": [
                {
                    "id": test.pk,
                    "name": test.name,
                    "category": test.category.name if test.category else "",
                    # Second chip on the card: what is collected.
                    "detail": test.sample_type,
                    "code": test.code,
                    "meta": test.turnaround and f"TAT {test.turnaround}",
                    "mrp": test.mrp if test.mrp is not None else test.price,
                    "partner_price": test.partner_price,
                    "is_available": test.is_active,
                }
                for test in tests
            ],
        }

    def _save_tests(self, provider, wanted) -> int:
        updated = 0
        for test in self._lab_tests(provider).filter(pk__in=wanted):
            # A shared-catalogue row is not this centre's to switch off — doing
            # so would take the test off every other lab in the city.
            if test.provider_id is None:
                continue
            if test.is_active != wanted[test.pk]:
                test.is_active = wanted[test.pk]
                test.save(update_fields=["is_active", "updated_at"])
                updated += 1
        return updated

    # -- medicines --------------------------------------------------------

    def _medicines(self, provider) -> dict:
        medicines = Medicine.objects.all().prefetch_related("category")
        listings = {
            listing.medicine_id: listing
            for listing in ProviderMedicine.objects.filter(provider=provider)
        }
        categories: set[str] = set()
        items = []
        for medicine in medicines:
            names = [category.name for category in medicine.category.all()]
            categories.update(names)
            listing = listings.get(medicine.pk)
            items.append(
                {
                    "id": medicine.pk,
                    "name": medicine.name,
                    "category": names[0] if names else "",
                    "detail": medicine.pack_unit,
                    "code": medicine.code,
                    "meta": medicine.content or "",
                    "mrp": medicine.mrp,
                    "partner_price": listing.partner_price if listing else medicine.price,
                    # No listing means the pharmacy has never answered about
                    # this medicine, which is not the same as stocking it.
                    "is_available": listing.is_available if listing else False,
                },
            )
        return {
            "kind": "medicines",
            "title": "Medicines Catalog",
            "search_placeholder": "Search for medicines",
            "categories": sorted(categories),
            "items": items,
        }

    def _save_medicines(self, provider, wanted) -> int:
        updated = 0
        for medicine_id, available in wanted.items():
            if not Medicine.objects.filter(pk=medicine_id).exists():
                continue
            listing, created = ProviderMedicine.objects.get_or_create(
                provider=provider,
                medicine_id=medicine_id,
                defaults={"is_available": available},
            )
            if created or listing.is_available != available:
                listing.is_available = available
                listing.save(update_fields=["is_available", "updated_at"])
                updated += 1
        return updated
