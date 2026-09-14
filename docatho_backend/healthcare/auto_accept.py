"""Requests the platform accepts on a partner's behalf once they run out of time.

Both partner screens show a countdown above an unanswered request — "Consultation
get auto accept in 02:30mins" — so the deadline is a promise made to the patient,
not a hint to the doctor. Something has to keep it.

The sweep runs whenever a partner's queue is read, which is the moment the
answer matters and costs one bulk UPDATE. The management command exists so ops
can run the same sweep on a schedule if a partner never opens the app.

ponytail: opportunistic sweep on read, no scheduler. If a request's status has
to be correct for a partner who is not looking at it — a report, a payout run —
put `auto_accept_overdue` behind a cron or Celery beat entry instead.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone


def is_enabled() -> bool:
    """Auto-accept is opt-in. Zero minutes means a human answers everything."""
    return settings.AUTO_ACCEPT_MINUTES > 0


def auto_accept_deadline(created_at):
    """When an unanswered request stops being the partner's to decide.

    None when the feature is off, which is what the apps key their countdown
    banner off — no deadline, no promise, no banner.
    """
    if not is_enabled():
        return None
    return created_at + timedelta(minutes=settings.AUTO_ACCEPT_MINUTES)


def auto_accept_overdue() -> dict[str, int]:
    """Accept every request whose deadline has passed. Returns what moved."""
    if not is_enabled():
        return {"appointments": 0, "bookings": 0}

    from .models import Appointment
    from .models import AppointmentStatus
    from .models import DiagnosticBooking
    from .models import DiagnosticBookingStatus

    cutoff = timezone.now() - timedelta(minutes=settings.AUTO_ACCEPT_MINUTES)

    appointments = Appointment.objects.filter(
        status=AppointmentStatus.PENDING,
        created_at__lte=cutoff,
    ).update(status=AppointmentStatus.CONFIRMED, updated_at=timezone.now())

    bookings = DiagnosticBooking.objects.filter(
        status=DiagnosticBookingStatus.REQUESTED,
        created_at__lte=cutoff,
    ).update(status=DiagnosticBookingStatus.CONFIRMED, updated_at=timezone.now())

    return {"appointments": appointments, "bookings": bookings}
