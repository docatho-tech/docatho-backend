"""
Status *buckets* — the three-way split the admin queues are worked in.

Every queue screen splits its rows the same way: what is still coming, what
finished, and what fell over. Those are groups of statuses, not statuses:
"Upcoming" is pending *and* confirmed *and* in progress, and "Cancelled"
covers both a patient cancelling and a doctor rejecting.

DRF's `filterset_fields` can only match one exact value, so a queue built on
it had to show one status at a time — which is why the tab strips could not
be built without this. Filtering client-side was the other option, and it is
the worse one: it would hide rows from the current page and still report the
unfiltered total in the header and the pagination.
"""

from django.db.models import QuerySet

#: Appointment statuses, grouped. Values match `AppointmentStatus`.
APPOINTMENT_BUCKETS = {
    "upcoming": ["pending", "confirmed", "in_progress"],
    "completed": ["completed"],
    "cancelled": ["cancelled", "rejected"],
}

#: Diagnostic booking statuses, grouped. Values match `DiagnosticBookingStatus`.
BOOKING_BUCKETS = {
    "new": [
        "requested",
        "confirmed",
        "slot_allotted",
        "assigned",
        "patient_arrived",
        "sample_collected",
        "in_progress",
        "test_done",
    ],
    "completed": ["report_generated", "completed"],
    "cancelled": ["cancelled"],
}

#: Order statuses, grouped. Values match `Order.Status`.
ORDER_BUCKETS = {
    "new": [
        "placed",
        "approved",
        "confirmed",
        "processing",
        "packed",
        "out_for_delivery",
    ],
    "completed": ["delivered"],
    "cancelled": ["cancelled", "rejected", "returned"],
}


#: Support ticket statuses, grouped. Values match `SupportTicketStatus`.
TICKET_BUCKETS = {
    "open": ["open", "in_progress"],
    "resolved": ["resolved", "closed"],
}


def apply_bucket(
    queryset: QuerySet,
    bucket: str | None,
    buckets: dict[str, list[str]],
    field: str = "status",
) -> QuerySet:
    """
    Narrow `queryset` to one bucket, or leave it alone.

    An unknown bucket name returns the queryset untouched rather than an empty
    list: a typo in a URL should show everything, not an empty table that
    looks like the queue has been cleared.

    Statuses a model does not actually define are simply absent from the data,
    so listing a few extra spellings per bucket costs nothing and means a
    status added later lands in a bucket instead of vanishing from every tab.
    """
    if not bucket:
        return queryset
    values = buckets.get(bucket)
    if not values:
        return queryset
    return queryset.filter(**{f"{field}__in": values})
