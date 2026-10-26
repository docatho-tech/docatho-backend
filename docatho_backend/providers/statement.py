"""The earnings statement as CSV — what "Download Statement" and "Export" fetch.

One endpoint for both buttons, because they ask the same question over
different windows: the payout card wants the month behind a transfer, the
Insights header wants whatever range the picker is showing. A partner opens it
in a spreadsheet next to their bank statement, so the rows are the lines and
the last row is the total they should be able to tie out to.
"""

from __future__ import annotations

import csv
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from rest_framework.views import APIView

from docatho_backend.masters.permissions import IsProvider
from docatho_backend.providers.earnings import earnings_lines
from docatho_backend.providers.partner_screens import _provider_for
from docatho_backend.providers.partner_screens import resolve_window

COLUMNS = ["Date", "Type", "Name", "Mode", "Amount", "Platform fee", "Net pay"]


def _when(value: str) -> str:
    return timezone.localtime(datetime.fromisoformat(value)).strftime("%d %b %Y %H:%M")


def _mode(value: str) -> str:
    return (value or "").replace("_", " ").title()


class ProviderStatementAPIView(APIView):
    """`GET /api/providers/statement/?period=…` or `?start=&end=` — CSV.

    CSV rather than a PDF: this is a reconciliation document, and the partner's
    next move with it is to sort, filter or sum a column. A PDF would only look
    more official.
    """

    permission_classes = [IsProvider]

    def get(self, request):
        provider = _provider_for(request.user)
        days, period = resolve_window(request.query_params)
        start, end = days[0], days[-1] + timedelta(days=1)
        # No limit: the screen shows the most recent fifty lines, but a
        # statement that silently stops at fifty cannot be reconciled.
        lines = earnings_lines(provider, since=start, until=end, limit=None)

        label = f"{start.date().isoformat()}_to_{days[-1].date().isoformat()}"
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="statement-{label}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow([f"Docatho earnings statement ({period})", label])
        writer.writerow([])
        writer.writerow(COLUMNS)

        totals = [Decimal("0"), Decimal("0"), Decimal("0")]
        for line in lines:
            amounts = [
                Decimal(line["amount"]),
                Decimal(line["platform_fee"]),
                Decimal(line["net"]),
            ]
            totals = [
                running + value
                for running, value in zip(totals, amounts, strict=True)
            ]
            writer.writerow(
                [
                    _when(line["at"]),
                    line["kind"],
                    line["name"],
                    _mode(line["mode"]),
                    *[str(value) for value in amounts],
                ],
            )

        writer.writerow([])
        writer.writerow(["Total", "", "", "", *[str(value) for value in totals]])
        return response
