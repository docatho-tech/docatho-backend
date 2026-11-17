"""Video consultation helpers — payment gating + 100ms room tokens."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .hms import HMSClient
from .hms import HMSNotConfiguredError
from .models import Appointment
from .models import AppointmentPaymentStatus
from .models import AppointmentStatus

#: How early the room opens, and how long after the slot it stays joinable.
CALL_WINDOW_BEFORE = timedelta(minutes=15)
CALL_WINDOW_AFTER = timedelta(hours=2)


def appointment_in_call_window(appointment: Appointment) -> bool:
    """Allow join from 15 minutes before scheduled time until 2 hours after."""
    if not appointment.scheduled_at:
        return False
    now = timezone.now()
    start = appointment.scheduled_at - CALL_WINDOW_BEFORE
    end = appointment.scheduled_at + CALL_WINDOW_AFTER
    return start <= now <= end


def patient_can_join_video(appointment: Appointment) -> bool:
    if appointment.consultation_mode != "online":
        return False
    if appointment.payment_status != AppointmentPaymentStatus.PAID:
        return False
    if appointment.status not in (
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.IN_PROGRESS,
    ):
        return False
    return appointment_in_call_window(appointment)


def provider_can_join_video(appointment: Appointment) -> bool:
    if appointment.consultation_mode != "online":
        return False
    if appointment.payment_status != AppointmentPaymentStatus.PAID:
        return False
    if appointment.status not in (
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.IN_PROGRESS,
    ):
        return False
    return appointment_in_call_window(appointment)


def ensure_video_room(appointment: Appointment) -> str:
    if appointment.video_room_id:
        return appointment.video_room_id

    room_name = f"docatho-appt-{appointment.pk}"
    client = HMSClient()
    if client.is_configured:
        room = client.create_room(room_name)
        room_id = room.get("id") or room.get("room_id") or ""
    else:
        room_id = f"dev-room-{appointment.pk}"

    appointment.video_room_id = room_id
    appointment.save(update_fields=["video_room_id", "updated_at"])
    return room_id


def mint_video_token(appointment: Appointment, *, user, role: str) -> dict:
    room_id = ensure_video_room(appointment)
    client = HMSClient()
    user_id = f"user-{user.pk}"
    try:
        token = (
            client.auth_token(room_id, user_id, role)
            if client.is_configured
            else client.dev_mock_token(room_id, user_id, role)
        )
        mock = not client.is_configured
    except HMSNotConfiguredError:
        token = client.dev_mock_token(room_id, user_id, role)
        mock = True

    if appointment.status == AppointmentStatus.CONFIRMED:
        appointment.status = AppointmentStatus.IN_PROGRESS
        appointment.video_started_at = timezone.now()
        appointment.save(update_fields=["status", "video_started_at", "updated_at"])

    return {
        "auth_token": token,
        "room_id": room_id,
        "role": role,
        "user_name": getattr(user, "name", None) or f"User {user.pk}",
        "appointment_id": appointment.pk,
        "mock": mock,
        "hms_configured": client.is_configured,
    }


def close_stale_video_consultations(*, grace: timedelta | None = None) -> int:
    """Close online consultations left running past their join window.

    A consultation normally ends when the doctor leaves the room. Nothing ends
    one where the app was killed mid-call, or where the patient opened the room
    and nobody ever joined — and `in_progress` is the one status from which the
    appointment can never move on its own: the doctor's queue keeps showing it
    and the patient cannot rate it.

    Past `scheduled_at + CALL_WINDOW_AFTER` nobody can rejoin anyway, so the
    consultation is over whatever happened inside it. This marks it completed
    and stamps `video_ended_at`.

    Known ceiling: a doctor no-show is indistinguishable from a finished call
    here, because only the patient's join is recorded (`video_started_at`), so
    both close as completed. Separating them — and refunding the no-shows —
    needs the doctor's join stamped too, or 100ms session webhooks.
    """
    # Imported here to match `reminders`: `views` is what owns the notification
    # helpers and it imports this module.
    from docatho_backend.healthcare.views import _notify_appointment_status_change

    grace = CALL_WINDOW_AFTER if grace is None else grace
    cutoff = timezone.now() - grace
    stale = Appointment.objects.filter(
        consultation_mode="online",
        status=AppointmentStatus.IN_PROGRESS,
        scheduled_at__lt=cutoff,
    ).select_related("patient", "doctor__provider")

    closed = 0
    for appointment in stale:
        now = timezone.now()
        appointment.status = AppointmentStatus.COMPLETED
        appointment.completed_at = now
        update_fields = ["status", "completed_at", "updated_at"]
        if appointment.video_ended_at is None:
            appointment.video_ended_at = now
            update_fields.append("video_ended_at")
        appointment.save(update_fields=update_fields)
        _notify_appointment_status_change(appointment, AppointmentStatus.COMPLETED)
        closed += 1
    return closed
