"""Put one doctor, one diagnostic centre and one pharmacy into every state the
partner app can show, so the screens can be walked end to end.

The ordinary seeds fill the database with plausible rows, which is not the same
thing: a queue of four confirmed appointments never renders the accept banner,
the payment summary or the countdown, and a pharmacy with no rider assigned
never renders the in-transit card. This command reaches into three accounts and
makes each screen state reachable by name.

Idempotent — run it as often as you like; it rewrites the same rows.

    uv run python manage.py seed_provider_demo
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import DiagnosticBooking
from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.healthcare.models import DiagnosticTest
from docatho_backend.healthcare.models import DoctorProfile
from docatho_backend.healthcare.models import VisitType
from docatho_backend.orders.models import Order
from docatho_backend.providers.models import Provider
from docatho_backend.users.models import User

DOCTOR_PHONE = "+917700009002"
CENTRE_PHONE = "+919700000000"
PHARMACY_PHONE = "+919811000001"

# Everyone signs in with this; `generate_otp` is pinned to it for local work.
OTP = "1234"

SAMPLE_TYPES = ["Blood", "Urine", "Tissue", "Swab"]


class Command(BaseCommand):
    help = "Make every partner-app screen state reachable on three demo accounts."

    def handle(self, *args, **options):
        self.stdout.write("Seeding partner demo accounts…\n")
        self.seed_patients()
        doctor = self.seed_doctor()
        centre = self.seed_centre()
        pharmacy = self.seed_pharmacy()

        self.stdout.write(self.style.SUCCESS("\nSign in with OTP " + OTP + ":\n"))
        for label, provider in (
            ("Doctor", doctor),
            ("Diagnostic Centre", centre),
            ("Pharmacy", pharmacy),
        ):
            if provider is None:
                self.stdout.write(self.style.WARNING(f"  {label:18} — account not found, skipped"))
                continue
            self.stdout.write(f"  {label:18} {provider.user.phone}   {provider.name}")

    # ------------------------------------------------------------------ #
    def seed_patients(self):
        """Give every patient an age, a sex and a photo.

        The patient card is half of most screens and reads as broken without
        them — the design shows "28 y • Female" beside a portrait.
        """
        today = timezone.localdate()
        for index, user in enumerate(User.objects.filter(provider__isnull=True, is_staff=False)):
            user.gender = "female" if index % 2 else "male"
            if user.dob is None:
                user.dob = today - timedelta(days=365 * (23 + (index * 5) % 40) + 10)
            user.save(update_fields=["gender", "dob"])

    def _provider(self, phone, city="Hyderabad", location="Kanajiguda"):
        provider = Provider.objects.filter(user__phone=phone).first()
        if provider is None:
            return None
        # The home header greets by name and shows the branch underneath.
        provider.city = provider.city or city
        provider.location = provider.location or location
        provider.save(update_fields=["city", "location"])
        return provider

    # ------------------------------------------------------------------ #
    def seed_doctor(self):
        provider = self._provider(DOCTOR_PHONE)
        if provider is None:
            return None
        profile = DoctorProfile.objects.filter(provider=provider).first()
        if profile is None:
            return provider

        appointments = list(Appointment.objects.filter(doctor=profile).order_by("pk"))
        patients = list(User.objects.filter(provider__isnull=True, is_staff=False)[:6])
        now = timezone.now()

        # Top up to five so there is one appointment per state plus history.
        while len(appointments) < 5:
            appointments.append(
                Appointment.objects.create(
                    patient=patients[len(appointments) % len(patients)],
                    doctor=profile,
                    scheduled_at=now + timedelta(hours=len(appointments)),
                    consultation_mode=ConsultationMode.IN_CLINIC,
                    fee=Decimal("700.00"),
                ),
            )

        states = [
            # Pending: the amber auto-accept banner, the payment summary and
            # Reject / Accept. `created_at` is aged so the countdown is partway.
            {
                "status": AppointmentStatus.PENDING,
                "mode": ConsultationMode.IN_CLINIC,
                "payment": AppointmentPaymentStatus.PENDING,
                "at": now + timedelta(hours=2, minutes=32),
                "aged": timedelta(minutes=30),
            },
            # Accepted but not open yet: the greyed "Starts in …" button.
            {
                "status": AppointmentStatus.CONFIRMED,
                "mode": ConsultationMode.ONLINE,
                "payment": AppointmentPaymentStatus.PAID,
                "at": now + timedelta(hours=2, minutes=32),
            },
            # Patient waiting in session: Join Consultation.
            {
                "status": AppointmentStatus.IN_PROGRESS,
                "mode": ConsultationMode.ONLINE,
                "payment": AppointmentPaymentStatus.PAID,
                "at": now + timedelta(minutes=5),
            },
            # Tomorrow, so the "Upcoming Appointments" card has a row.
            {
                "status": AppointmentStatus.CONFIRMED,
                "mode": ConsultationMode.IN_CLINIC,
                "payment": AppointmentPaymentStatus.PAID,
                "at": now + timedelta(days=1, hours=1),
            },
            # History, so "Previous consultations" is not empty.
            {
                "status": AppointmentStatus.COMPLETED,
                "mode": ConsultationMode.ONLINE,
                "payment": AppointmentPaymentStatus.PAID,
                "at": now - timedelta(days=30),
            },
        ]

        # One patient across the board, so the history card has something to show
        # on the appointment that is open now. Named, because the seeded fixtures
        # carry machine names ("E2E Editable 1786…") that fill the header bar.
        patient = patients[0]
        patient.name = "Anjali Sharma"
        patient.gender = "female"
        patient.dob = timezone.localdate() - timedelta(days=365 * 28 + 7)
        patient.save(update_fields=["name", "gender", "dob"])
        for appointment, state in zip(appointments, states, strict=False):
            appointment.patient = patient
            appointment.status = state["status"]
            appointment.consultation_mode = state["mode"]
            appointment.payment_status = state["payment"]
            appointment.scheduled_at = state["at"]
            appointment.fee = Decimal("700.00")
            appointment.platform_fee = Decimal("11.00")
            appointment.save()
            if state.get("aged"):
                Appointment.objects.filter(pk=appointment.pk).update(
                    created_at=timezone.now() - state["aged"],
                )
        return provider

    # ------------------------------------------------------------------ #
    def seed_centre(self):
        provider = self._provider(CENTRE_PHONE)
        if provider is None:
            return None

        # Every test row shows a category chip and a sample chip.
        for index, test in enumerate(DiagnosticTest.objects.all()):
            if not test.sample_type:
                test.sample_type = SAMPLE_TYPES[index % len(SAMPLE_TYPES)]
                test.save(update_fields=["sample_type"])

        tests = list(DiagnosticTest.objects.all()[:3])
        patients = list(User.objects.filter(provider__isnull=True, is_staff=False)[:4])
        bookings = list(DiagnosticBooking.objects.filter(center=provider).order_by("pk"))
        today = timezone.localdate()

        while len(bookings) < 4:
            bookings.append(
                DiagnosticBooking.objects.create(
                    patient=patients[len(bookings) % len(patients)],
                    center=provider,
                    total_amount=Decimal("1499.00"),
                ),
            )

        states = [
            # Unanswered: the banner and Reject / Accept.
            {
                "status": DiagnosticBookingStatus.REQUESTED,
                "visit": VisitType.AT_CENTRE,
                "date": today,
                "time": "09:30",
                "aged": timedelta(minutes=30),
            },
            # Accepted: Upload Reports.
            {
                "status": DiagnosticBookingStatus.CONFIRMED,
                "visit": VisitType.AT_HOME,
                "date": today,
                "time": "11:30",
            },
            {
                "status": DiagnosticBookingStatus.SAMPLE_COLLECTED,
                "visit": VisitType.AT_CENTRE,
                "date": today,
                "time": "14:30",
            },
            # Tomorrow, for the second day card.
            {
                "status": DiagnosticBookingStatus.CONFIRMED,
                "visit": VisitType.AT_HOME,
                "date": today + timedelta(days=1),
                "time": "10:30",
            },
        ]

        for booking, state in zip(bookings, states, strict=False):
            booking.status = state["status"]
            booking.visit_type = state["visit"]
            booking.scheduled_date = state["date"]
            booking.scheduled_time = state["time"]
            booking.patient_address = "123, Mehdipatnam, Hyderabad"
            booking.save()
            booking.tests.set(tests)
            if state.get("aged"):
                DiagnosticBooking.objects.filter(pk=booking.pk).update(
                    created_at=timezone.now() - state["aged"],
                )
        return provider

    # ------------------------------------------------------------------ #
    def seed_pharmacy(self):
        provider = self._provider(PHARMACY_PHONE)
        if provider is None:
            return None

        orders = list(Order.objects.filter(assigned_provider=provider).order_by("pk"))
        states = [
            # New: checkboxes, address, phone, total bill, Reject / Accept.
            {"status": Order.Status.PLACED},
            # In transit: the dark chip, rider, ETA, Mark Delivered.
            {
                "status": Order.Status.OUT_FOR_DELIVERY,
                "rider_name": "I. Prakash",
                "rider_code": "RDR-218",
                "eta": 22,
            },
            # Delivered: green chip, delivered-by, Paid • Prepaid, invoice.
            {
                "status": Order.Status.DELIVERED,
                "rider_name": "S. Kumar",
                "rider_code": "RDR-219",
                "eta": 14,
                "delivered": True,
            },
            {"status": Order.Status.APPROVED},
        ]

        for order, state in zip(orders, states, strict=False):
            order.status = state["status"]
            order.rider_name = state.get("rider_name", "")
            order.rider_code = state.get("rider_code", "")
            order.estimated_delivery_mins = state.get("eta", 0)
            order.payment_status = Order.PaymentStatus.PAID
            order.payment_method = Order.PaymentMethod.ONLINE
            order.delivered_at = timezone.now() - timedelta(hours=2) if state.get("delivered") else None
            order.save()
        return provider
