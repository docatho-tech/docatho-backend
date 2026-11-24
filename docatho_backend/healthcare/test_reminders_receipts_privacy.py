"""Three things that were missing rather than wrong.

No reminder ever fired (the sweep had no schedule and only told the doctor),
a paid consultation produced no receipt at all, and a prescription PDF was
addressed by a permanent public URL.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.storage import storages
from django.utils import timezone

from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentPrescription
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.prescription_api import (
    AppointmentPrescriptionSerializer,
)
from docatho_backend.healthcare.private_media import PRIVATE_PATH_PREFIX
from docatho_backend.healthcare.private_media import PRIVATE_REF_PREFIX
from docatho_backend.healthcare.private_media import signed_url
from docatho_backend.healthcare.private_media import stored_file_url
from docatho_backend.orders.models import Prescription
from docatho_backend.orders.views import PrescriptionSerializer
from docatho_backend.healthcare.reminders import send_due_reminders
from docatho_backend.notifications.models import Notification
from docatho_backend.notifications.models import NotificationType
from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import UserFactory

def make_appointment(**kwargs):
    """An appointment with a doctor whose provider has a real user account."""
    kwargs.setdefault("patient", UserFactory())
    kwargs.setdefault("doctor", DoctorProfileFactory())
    kwargs.setdefault("scheduled_at", timezone.now() + timedelta(minutes=20))
    kwargs.setdefault("consultation_mode", ConsultationMode.ONLINE)
    kwargs.setdefault("status", AppointmentStatus.CONFIRMED)
    kwargs.setdefault("fee", Decimal("400.00"))
    kwargs.setdefault("payment_method", "online")
    return Appointment.objects.create(**kwargs)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #
def test_the_reminder_sweep_is_actually_scheduled():
    """It only ever ran when somebody remembered to run the command."""
    entry = settings.CELERY_BEAT_SCHEDULE["send-appointment-reminders"]
    assert entry["task"] == (
        "docatho_backend.healthcare.tasks.send_appointment_reminders"
    )
    # Must sweep more often than the window it looks at, or an appointment
    # booked just after a pass is never reminded.
    assert entry["schedule"] < 30 * 60


def test_reminder_reaches_the_patient_and_the_doctor_once():
    appointment = make_appointment(
        status=AppointmentStatus.CONFIRMED,
        scheduled_at=timezone.now() + timedelta(minutes=20),
    )

    assert send_due_reminders() == 1

    reminders = Notification.objects.filter(
        notification_type=NotificationType.APPOINTMENT_REMINDER,
        data__appointment_id=appointment.pk,
    )
    recipients = set(reminders.values_list("recipient_id", flat=True))
    assert appointment.patient_id in recipients, "the patient must be reminded too"
    assert appointment.doctor.provider.user_id in recipients

    # A second sweep in the same window is a no-op, not a second buzz.
    assert send_due_reminders() == 0
    assert reminders.count() == len(recipients)


def test_reminder_skips_an_appointment_outside_the_window():
    make_appointment(
        status=AppointmentStatus.CONFIRMED,
        scheduled_at=timezone.now() + timedelta(hours=4),
    )
    assert send_due_reminders() == 0


# --------------------------------------------------------------------------- #
# Consultation receipt
# --------------------------------------------------------------------------- #
def test_patient_downloads_the_receipt_for_a_paid_consultation(auth_client):
    appointment = make_appointment(
        status=AppointmentStatus.COMPLETED,
        payment_status=AppointmentPaymentStatus.PAID,
        fee=Decimal("400.00"),
        platform_fee=Decimal("40.00"),
    )

    resp = auth_client(appointment.patient).get(
        f"/api/healthcare/appointments/{appointment.pk}/receipt/",
    )

    assert resp.status_code == 200, resp.content[:200]
    assert resp["Content-Type"] == "application/pdf"
    assert b"%PDF" in b"".join(resp.streaming_content)[:8]


def test_no_receipt_before_the_money_arrives(auth_client):
    """A receipt claims a payment is held. Unpaid, there is nothing to claim."""
    appointment = make_appointment(
        status=AppointmentStatus.CONFIRMED,
        payment_status=AppointmentPaymentStatus.PENDING,
    )
    resp = auth_client(appointment.patient).get(
        f"/api/healthcare/appointments/{appointment.pk}/receipt/",
    )
    assert resp.status_code == 409, resp.data


def test_another_patient_cannot_read_a_receipt(auth_client):
    appointment = make_appointment(
        status=AppointmentStatus.COMPLETED,
        payment_status=AppointmentPaymentStatus.PAID,
    )
    stranger = make_appointment().patient

    resp = auth_client(stranger).get(
        f"/api/healthcare/appointments/{appointment.pk}/receipt/",
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Medical documents are not world-readable
# --------------------------------------------------------------------------- #
def test_prescription_pdf_lands_on_the_private_storage():
    appointment = make_appointment(status=AppointmentStatus.COMPLETED)
    record = AppointmentPrescription.objects.create(
        appointment=appointment,
        diagnosis="Acute pharyngitis",
    )
    record.pdf.save("rx.pdf", ContentFile(b"%PDF-1.4 test"), save=True)

    # The alias, not the prefix: the test settings swap both storages for an
    # in-memory one, so the prefix is only real on S3. What has to hold
    # everywhere is that the field is not on the public `default` storage.
    assert record.pdf.storage is storages["private"], (
        "a named patient's diagnosis must not go on the public media storage"
    )
    assert storages["private"] is not storages["default"]


def test_a_private_reference_is_signed_per_read_and_never_stored():
    appointment = make_appointment(status=AppointmentStatus.COMPLETED)
    record = AppointmentPrescription.objects.create(
        appointment=appointment,
        diagnosis="Acute pharyngitis",
        document_url=f"{PRIVATE_REF_PREFIX}documents/abc123.pdf",
    )

    served = AppointmentPrescriptionSerializer(record).data["document_url"]

    assert not served.startswith(PRIVATE_REF_PREFIX), "the key must not be served raw"
    assert served == signed_url(record.document_url)
    record.refresh_from_db()
    assert record.document_url.startswith(PRIVATE_REF_PREFIX), (
        "the signed URL expires, so it must never be what is persisted"
    )


def test_a_public_url_written_before_the_split_still_renders():
    """Documents already on the public prefix have to keep working."""
    legacy = "https://example.s3.amazonaws.com/media/documents/old.pdf"
    assert signed_url(legacy) == legacy


@pytest.mark.django_db
def test_a_patients_prescription_upload_lands_on_the_private_storage():
    prescription = Prescription.objects.create(user=UserFactory())
    prescription.image.save("rx.jpg", ContentFile(b"jpegbytes"), save=True)

    assert prescription.image.storage is storages["private"], (
        "a prescription names the patient and their drugs — not public storage"
    )
    assert prescription.image.name.startswith(PRIVATE_PATH_PREFIX), (
        "the key has to say which bucket it is in, or legacy rows break"
    )
    # Served signed, and read through the helper rather than `.url`.
    served = PrescriptionSerializer(prescription).data["image"]
    assert served == stored_file_url(prescription.image)


@pytest.mark.django_db
def test_a_prescription_uploaded_before_the_move_still_reads_from_public():
    """The bytes of an old row are only on the public prefix."""
    prescription = Prescription.objects.create(user=UserFactory())
    # No save() through the field: this is what the column already holds.
    Prescription.objects.filter(pk=prescription.pk).update(
        image="prescriptions/2026/11/old.jpg",
    )
    prescription.refresh_from_db()

    assert stored_file_url(prescription.image) == default_storage.url(
        "prescriptions/2026/11/old.jpg",
    )
