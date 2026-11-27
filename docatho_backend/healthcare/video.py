"""Video consultation helpers — payment gating + 100ms room tokens."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .hms import HMSClient
from .hms import HMSNotConfiguredError
from .hms import mock_tokens_allowed
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


#: How long after a consultation closes its thread stays writable, so a
#: follow-up question the same evening still reaches the doctor.
CHAT_WINDOW_AFTER_COMPLETION = timedelta(hours=24)


def consultation_chat_is_open(appointment: Appointment) -> bool:
    """Whether either side may still post to this appointment's thread.

    Lives beside the call window because it is the same window: the thread
    opens with the room, 15 minutes before the slot. The client had this rule
    off the device clock and the server had none at all, so a patient could
    message on a pending, rejected, cancelled or unpaid appointment, and
    forever after it finished — each one pushing the doctor.

    Zero-fee consultations are marked paid without the gateway
    (``settle_free_appointment``), so the payment check does not shut them out.
    """
    if appointment.status in (
        AppointmentStatus.PENDING,
        AppointmentStatus.REJECTED,
        AppointmentStatus.CANCELLED,
    ):
        return False
    if appointment.payment_status != AppointmentPaymentStatus.PAID:
        return False
    if not appointment.scheduled_at:
        return False

    now = timezone.now()
    if now < appointment.scheduled_at - CALL_WINDOW_BEFORE:
        return False
    if (
        appointment.completed_at
        and now > appointment.completed_at + CHAT_WINDOW_AFTER_COMPLETION
    ):
        return False
    return True


def patient_can_join_video(appointment: Appointment) -> bool:
    """The patient joins a call the doctor has already started, never opens one.

    `video_started_at` is stamped by `mint_video_token`, and the patient can no
    longer mint before it is set, so it can only have been set by the doctor —
    no extra field is needed to record who opened the room.
    """
    if appointment.consultation_mode != "online":
        return False
    if appointment.payment_status != AppointmentPaymentStatus.PAID:
        return False
    if appointment.status not in (
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.IN_PROGRESS,
    ):
        return False
    if appointment.video_started_at is None:
        return False
    return appointment_in_call_window(appointment)


def doctor_opened_thread(appointment: Appointment) -> bool:
    """Whether the doctor has written in this thread.

    The doctor starts the conversation, so until there is a line from their
    side the patient may read the thread but not post to it. Any sender who is
    not the patient counts — staff answering on the doctor's behalf opens it
    too, the same way `ConsultationMessageSerializer` renders them as the
    doctor.
    """
    return appointment.messages.exclude(sender_id=appointment.patient_id).exists()


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
        # Raises when the mock is not allowed — before anything is written, so
        # an unconfigured server does not leave a fake room id on the record.
        if not mock_tokens_allowed():
            raise HMSNotConfiguredError
        room_id = f"dev-room-{appointment.pk}"

    appointment.video_room_id = room_id
    appointment.save(update_fields=["video_room_id", "updated_at"])
    return room_id


def mint_video_token(appointment: Appointment, *, user, role: str) -> dict:
    room_id = ensure_video_room(appointment)
    client = HMSClient()
    user_id = f"user-{user.pk}"
    # No fallback: `dev_mock_token` refuses outside DEBUG, and the
    # `HMSNotConfiguredError` it raises is a 503 the client can act on. Catching
    # it here is what minted the unjoinable token in production.
    token = (
        client.auth_token(room_id, user_id, role)
        if client.is_configured
        else client.dev_mock_token(room_id, user_id, role)
    )
    mock = not client.is_configured

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
