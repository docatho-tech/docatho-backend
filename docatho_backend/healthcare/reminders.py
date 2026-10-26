"""The half-hour reminder the partner app's notification list shows.

Dedupe is a query, not a column: a reminder already sent is a
:class:`Notification` row whose ``data.appointment_id`` is this appointment, and
asking that is cheaper than a ``reminder_sent_at`` field that has to be
backfilled, migrated and kept honest. Running the sweep twice in the same
window is therefore a no-op rather than a second buzz.
"""

from datetime import timedelta

from django.utils import timezone

from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType

#: How far ahead of the slot the reminder fires.
REMINDER_MINUTES = 30


def send_due_reminders(*, minutes: int = REMINDER_MINUTES) -> int:
    """Remind every doctor whose next appointment starts within `minutes`."""
    # Imported here: `views` imports this module's caller, and at module level
    # the two would close the cycle.
    from docatho_backend.healthcare.models import Appointment
    from docatho_backend.healthcare.models import AppointmentStatus
    from docatho_backend.healthcare.views import notify_appointment_reminder

    now = timezone.now()
    due = Appointment.objects.filter(
        status__in=[AppointmentStatus.CONFIRMED, AppointmentStatus.PENDING],
        scheduled_at__gt=now,
        scheduled_at__lte=now + timedelta(minutes=minutes),
    ).select_related("patient", "doctor__provider__user")

    sent = 0
    for appointment in due:
        already = Notification.objects.filter(
            notification_type=NotificationType.APPOINTMENT_REMINDER,
            data__appointment_id=appointment.pk,
        ).exists()
        if already:
            continue
        if notify_appointment_reminder(appointment) is not None:
            sent += 1
    return sent
