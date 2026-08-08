"""Video consultation helpers — payment gating + 100ms room tokens."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .hms import HMSClient
from .hms import HMSNotConfiguredError
from .models import Appointment
from .models import AppointmentPaymentStatus
from .models import AppointmentStatus


def appointment_in_call_window(appointment: Appointment) -> bool:
    """Allow join from 15 minutes before scheduled time until 2 hours after."""
    if not appointment.scheduled_at:
        return False
    now = timezone.now()
    start = appointment.scheduled_at - timedelta(minutes=15)
    end = appointment.scheduled_at + timedelta(hours=2)
    return start <= now <= end


def patient_can_join_video(appointment: Appointment) -> bool:
    if appointment.consultation_mode != "online":
        return False
    if appointment.payment_status != AppointmentPaymentStatus.PAID:
        return False
    if appointment.status not in (AppointmentStatus.CONFIRMED, AppointmentStatus.IN_PROGRESS):
        return False
    return appointment_in_call_window(appointment)


def provider_can_join_video(appointment: Appointment) -> bool:
    if appointment.consultation_mode != "online":
        return False
    if appointment.payment_status != AppointmentPaymentStatus.PAID:
        return False
    if appointment.status not in (AppointmentStatus.CONFIRMED, AppointmentStatus.IN_PROGRESS):
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
        token = client.auth_token(room_id, user_id, role) if client.is_configured else client.dev_mock_token(room_id, user_id, role)
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
