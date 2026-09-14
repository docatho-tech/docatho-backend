"""DRF views for Phase 1 healthcare: doctors, diagnostics, AI, admin."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from rest_framework import permissions
from rest_framework import serializers
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.generics import ListAPIView
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from docatho_backend.healthcare.appointment_payments import confirm_appointment_payment
from docatho_backend.healthcare.appointment_payments import create_appointment_checkout
from docatho_backend.healthcare.ai_service import HealthcareAIService
from docatho_backend.healthcare.auto_accept import auto_accept_deadline
from docatho_backend.healthcare.auto_accept import auto_accept_overdue
from docatho_backend.healthcare.video import mint_video_token
from docatho_backend.healthcare.video import patient_can_join_video
from docatho_backend.healthcare.video import provider_can_join_video
from docatho_backend.healthcare.models import AIChatMessage
from docatho_backend.healthcare.models import AIChatSession
from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import ConsultationMessage
from docatho_backend.healthcare.models import ConsultationMessageKind
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import ContentPage
from docatho_backend.healthcare.dashboard_stats import build_dashboard_stats
from docatho_backend.masters.buckets import APPOINTMENT_BUCKETS
from docatho_backend.masters.buckets import BOOKING_BUCKETS
from docatho_backend.masters.buckets import TICKET_BUCKETS
from docatho_backend.masters.buckets import apply_bucket
from docatho_backend.healthcare.models import DiagnosticBooking
from docatho_backend.healthcare.models import DiagnosticBookingKind
from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.healthcare.models import DiagnosticPackage
from docatho_backend.healthcare.models import DiagnosticTest
from docatho_backend.healthcare.models import DiagnosticTestCategory
from docatho_backend.healthcare.models import BlockedDate
from docatho_backend.healthcare.models import DoctorAvailability
from docatho_backend.healthcare.models import DoctorProfile
from docatho_backend.healthcare.models import ProviderAvailability
from docatho_backend.healthcare.models import MedicalSpecialty
from docatho_backend.healthcare.models import Qualification
from docatho_backend.healthcare.models import MedicineReminder
from docatho_backend.healthcare.models import SavedDoctor
from docatho_backend.healthcare.models import SupportTicket
from docatho_backend.healthcare.models import VerificationStatus
from docatho_backend.healthcare.models import WishlistItem
from docatho_backend.masters.permissions import IsAdmin
from docatho_backend.masters.permissions import IsCustomer
from docatho_backend.masters.permissions import IsProvider
from docatho_backend.masters.permissions import ReadOnlyOrAdmin
from docatho_backend.masters.permissions import is_provider
from docatho_backend.orders.models import Prescription
from docatho_backend.orders.paginators import GenericPaginationClass
from docatho_backend.notifications.models import NotificationType
from docatho_backend.notifications.services import notify
from docatho_backend.providers.enums import ProviderType
from docatho_backend.providers.models import Provider
from docatho_backend.users.models import User


class MedicalSpecialtySerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicalSpecialty
        fields = ("id", "name", "icon_url", "is_active")


class DoctorListSerializer(serializers.ModelSerializer):
    provider_id = serializers.IntegerField(source="provider.id", read_only=True)
    name = serializers.CharField(source="provider.name", read_only=True)
    specialty = serializers.CharField(source="provider.specialty", read_only=True)
    specialties = MedicalSpecialtySerializer(many=True, read_only=True)

    class Meta:
        model = DoctorProfile
        fields = (
            "id",
            "provider_id",
            "name",
            "specialty",
            "specialties",
            "biography",
            "qualifications",
            "qualifications_ug",
            "qualifications_pg",
            "experience_years",
            "languages",
            "fee_online",
            "fee_in_clinic",
            "fee_home_visit",
            "consultation_modes",
            "rating_avg",
            "review_count",
            "clinic_name",
            "clinic_city",
            "profile_picture",
            "is_online",
            "is_verified",
            "verification_status",
        )


class DoctorDetailSerializer(DoctorListSerializer):
    class Meta(DoctorListSerializer.Meta):
        fields = DoctorListSerializer.Meta.fields + (
            "clinic_address",
            "clinic_latitude",
            "clinic_longitude",
            "clinic_images",
        )


class AppointmentSerializer(serializers.ModelSerializer):
    doctor_name = serializers.CharField(source="doctor.provider.name", read_only=True)
    doctor_id = serializers.IntegerField(source="doctor.id", read_only=True)
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    # The provider app's patient card shows "28 y • Female" with a photo and
    # rings or navigates from the same card, so all five travel with the
    # appointment rather than costing a second round trip per row.
    patient_age = serializers.IntegerField(source="patient.age", read_only=True)
    patient_gender = serializers.CharField(source="patient.gender", read_only=True)
    patient_photo = serializers.CharField(
        source="patient.profile_picture",
        read_only=True,
    )
    patient_phone = serializers.SerializerMethodField()
    patient_address = serializers.SerializerMethodField()
    total_payable = serializers.SerializerMethodField()
    previous_consultations = serializers.SerializerMethodField()
    auto_accept_at = serializers.SerializerMethodField()
    can_join_video = serializers.SerializerMethodField()
    requires_payment = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = (
            "id",
            "doctor",
            "doctor_id",
            "doctor_name",
            "patient_name",
            "patient_age",
            "patient_gender",
            "patient_photo",
            "patient_phone",
            "patient_address",
            "platform_fee",
            "total_payable",
            "previous_consultations",
            "auto_accept_at",
            "scheduled_at",
            "consultation_mode",
            "status",
            "fee",
            "payment_method",
            "payment_status",
            "paid_at",
            "video_room_id",
            "video_started_at",
            "video_ended_at",
            "recording_url",
            "can_join_video",
            "requires_payment",
            "symptoms",
            "notes",
            "prescription_notes",
            "patient_rating",
            "patient_feedback",
            "completed_at",
            "created_at",
        )
        read_only_fields = (
            "id",
            "status",
            "fee",
            "payment_status",
            "paid_at",
            "video_room_id",
            "video_started_at",
            "video_ended_at",
            "recording_url",
            "prescription_notes",
            "completed_at",
            "created_at",
        )

    def get_auto_accept_at(self, obj: Appointment):
        """When this request gets accepted for the doctor, if they don't answer.

        Null once it has been answered — a countdown on a confirmed appointment
        would be counting down to nothing.
        """
        if obj.status != AppointmentStatus.PENDING:
            return None
        return auto_accept_deadline(obj.created_at)

    def get_patient_phone(self, obj: Appointment) -> str:
        return str(obj.patient.phone or "")

    def get_patient_address(self, obj: Appointment) -> str:
        address = obj.patient.address
        if address is None:
            return ""
        parts = [
            address.address_line1,
            address.address_line2,
            address.landmark,
            address.city,
        ]
        return ", ".join(p for p in parts if p)

    def get_total_payable(self, obj: Appointment) -> str:
        """Consultation fee plus the platform's cut — what the patient pays.

        Summed here rather than in each client: three apps render this line and
        two of them would round it differently.
        """
        return str(obj.fee + obj.platform_fee)

    def get_previous_consultations(self, obj: Appointment) -> list[dict]:
        """This patient's last few completed consultations, with any doctor.

        The provider app shows them on the appointment detail so the doctor can
        see who has already seen this patient. Capped at five: it is context on
        a card, not a medical history screen.

        Only the detail views ask for it. Lists pass no flag and get an empty
        list, because filling it there is one extra query per row for something
        no list renders.
        """
        if not self.context.get("with_history"):
            return []
        past = (
            Appointment.objects.filter(
                patient_id=obj.patient_id,
                status=AppointmentStatus.COMPLETED,
            )
            .exclude(pk=obj.pk)
            .select_related("doctor__provider")
            .order_by("-scheduled_at")[:5]
        )
        return [
            {
                "id": appointment.pk,
                "doctor_name": appointment.doctor.provider.name,
                "doctor_specialty": appointment.doctor.provider.specialty,
                "doctor_photo": appointment.doctor.profile_picture,
                "scheduled_at": appointment.scheduled_at,
            }
            for appointment in past
        ]

    def get_can_join_video(self, obj: Appointment) -> bool:
        request = self.context.get("request")
        if request and is_provider(request.user):
            return provider_can_join_video(obj)
        return patient_can_join_video(obj)

    def get_requires_payment(self, obj: Appointment) -> bool:
        return (
            obj.consultation_mode == ConsultationMode.ONLINE
            and obj.payment_status != AppointmentPaymentStatus.PAID
            and obj.status
            not in (
                AppointmentStatus.CANCELLED,
                AppointmentStatus.REJECTED,
                AppointmentStatus.COMPLETED,
            )
        )


class AdminAppointmentSerializer(AppointmentSerializer):
    """Staff view of an appointment.

    `status` is unlocked so support can confirm, complete or reject on a
    patient's behalf; the patient-facing serializer keeps it read-only so a
    customer cannot mark their own consultation completed.

    The extra read-only fields are what the admin queue's columns are: the
    city a consultation belongs to, who to ring, and the doctor's specialty.
    They are on the staff serializer only — a patient listing their own
    appointments has no business receiving the doctor's contact details.
    """

    patient_id = serializers.IntegerField(source="patient.id", read_only=True)
    patient_phone = serializers.CharField(source="patient.phone", read_only=True)
    doctor_specialty = serializers.CharField(
        source="doctor.provider.specialty",
        read_only=True,
    )
    doctor_picture = serializers.CharField(
        source="doctor.profile_picture",
        read_only=True,
    )
    doctor_phone = serializers.CharField(
        source="doctor.provider.user.phone",
        read_only=True,
    )
    city = serializers.CharField(source="doctor.clinic_city", read_only=True)
    prescription_url = serializers.SerializerMethodField()
    message_count = serializers.SerializerMethodField()

    def get_prescription_url(self, obj):
        """
        The patient's most recent prescription document.

        `Prescription` is not linked to an appointment — it is uploaded at
        pharmacy checkout — so this is the latest one on file rather than a
        claim that it came out of this consultation.
        """
        prescription = obj.patient.prescriptions.order_by("-created_at").first()
        if prescription is None or not prescription.image:
            return ""
        request = self.context.get("request")
        url = prescription.image.url
        return request.build_absolute_uri(url) if request else url

    def get_message_count(self, obj):
        return obj.messages.count()

    class Meta(AppointmentSerializer.Meta):
        fields = AppointmentSerializer.Meta.fields + (
            "patient_id",
            "patient_phone",
            "doctor_specialty",
            "doctor_picture",
            "doctor_phone",
            "city",
            "prescription_url",
            "message_count",
        )
        read_only_fields = tuple(
            field
            for field in AppointmentSerializer.Meta.read_only_fields
            if field != "status"
        )


class AppointmentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Appointment
        fields = (
            "doctor",
            "scheduled_at",
            "consultation_mode",
            "symptoms",
            "payment_method",
        )

    def validate(self, attrs):
        doctor = attrs["doctor"]
        if not doctor.is_verified:
            raise serializers.ValidationError({"doctor": "Doctor not verified."})
        mode = attrs["consultation_mode"]
        fee_map = {
            ConsultationMode.ONLINE: doctor.fee_online,
            ConsultationMode.IN_CLINIC: doctor.fee_in_clinic,
            ConsultationMode.HOME_VISIT: doctor.fee_home_visit,
        }
        attrs["fee"] = fee_map.get(mode, doctor.fee_online)
        return attrs

    def create(self, validated_data):
        validated_data["patient"] = self.context["request"].user
        if validated_data.get("consultation_mode") == ConsultationMode.ONLINE:
            if validated_data.get("payment_method") in ("", "pay_at_clinic", "cod"):
                validated_data["payment_method"] = "online"
        return super().create(validated_data)


class DiagnosticTestCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticTestCategory
        fields = ("id", "name", "icon_url", "is_active")


class DiagnosticTestSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(
        source="category.name", read_only=True, default=None
    )
    provider_name = serializers.CharField(
        source="provider.name", read_only=True, default=None
    )
    discount_percent = serializers.IntegerField(read_only=True)

    class Meta:
        model = DiagnosticTest
        fields = (
            "id",
            "name",
            "category",
            "category_name",
            "provider",
            "provider_name",
            "description",
            "price",
            "mrp",
            "discount_percent",
            "sample_type",
            "preparation_instructions",
            "images",
            "is_active",
            "test_kind",
        )

    def validate(self, attrs):
        return _validate_list_price(attrs, self.instance)


def _validate_list_price(attrs, instance):
    """An MRP below the price is a discount that reads as a markup."""
    price = attrs.get("price", getattr(instance, "price", None))
    mrp = attrs.get("mrp", getattr(instance, "mrp", None))
    if price is not None and mrp is not None and mrp < price:
        raise serializers.ValidationError(
            {"mrp": "MRP cannot be lower than the price."}
        )
    return attrs


class DiagnosticPackageSerializer(serializers.ModelSerializer):
    test_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=DiagnosticTest.objects.all(),
        source="tests",
        write_only=True,
    )
    tests = DiagnosticTestSerializer(many=True, read_only=True)
    provider_name = serializers.CharField(
        source="provider.name", read_only=True, default=None
    )
    discount_percent = serializers.IntegerField(read_only=True)
    tests_total = serializers.SerializerMethodField()

    class Meta:
        model = DiagnosticPackage
        fields = (
            "id",
            "name",
            "provider",
            "provider_name",
            "description",
            "tests",
            "test_ids",
            "price",
            "mrp",
            "discount_percent",
            "tests_total",
            "preparation_instructions",
            "images",
            "is_active",
        )

    def get_tests_total(self, obj) -> str:
        """What the same tests cost bought separately — the saving, priced."""
        return str(sum((test.price for test in obj.tests.all()), Decimal("0")))

    def validate_test_ids(self, tests):
        # A pack of one is a test with a second price, and two rows that
        # disagree about what a thing costs is the bug that follows.
        if len(tests) < 2:
            raise serializers.ValidationError("A package needs at least two tests.")
        return tests

    def validate(self, attrs):
        return _validate_list_price(attrs, self.instance)


class DiagnosticBookingSerializer(serializers.ModelSerializer):
    test_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=DiagnosticTest.objects.filter(is_active=True),
        source="tests",
        write_only=True,
    )
    tests = DiagnosticTestSerializer(many=True, read_only=True)
    package_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=DiagnosticPackage.objects.filter(is_active=True),
        source="packages",
        write_only=True,
        required=False,
    )
    packages = DiagnosticPackageSerializer(many=True, read_only=True)
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    patient_phone = serializers.CharField(source="patient.phone", read_only=True)
    # The centre's booking card shows the patient the same way the doctor's
    # does — "28 y • Female" beside a photo.
    patient_age = serializers.IntegerField(source="patient.age", read_only=True)
    patient_gender = serializers.CharField(source="patient.gender", read_only=True)
    patient_photo = serializers.CharField(
        source="patient.profile_picture",
        read_only=True,
    )
    center_name = serializers.CharField(source="center.name", read_only=True, default="")
    center_logo = serializers.CharField(
        source="center.logo_url",
        read_only=True,
        default="",
    )
    # The centre's city, not the patient's: the queues are worked per city and
    # the row names the lab the sample goes to.
    city = serializers.CharField(source="center.city", read_only=True, default="")
    test_count = serializers.SerializerMethodField()
    auto_accept_at = serializers.SerializerMethodField()

    def get_test_count(self, obj):
        return obj.tests.count()

    def get_auto_accept_at(self, obj):
        """When an unanswered booking gets accepted for the centre. See
        `auto_accept.py` — null once someone has answered it."""
        if obj.status != DiagnosticBookingStatus.REQUESTED:
            return None
        return auto_accept_deadline(obj.created_at)

    class Meta:
        model = DiagnosticBooking
        fields = (
            "id",
            "center",
            "center_name",
            "center_logo",
            "city",
            "tests",
            "test_ids",
            "test_count",
            "packages",
            "package_ids",
            "kind",
            "visit_type",
            "status",
            "scheduled_date",
            "scheduled_time",
            "total_amount",
            "patient_address",
            "notes",
            "reports",
            "auto_accept_at",
            "created_at",
            "patient_name",
            "patient_phone",
            "patient_age",
            "patient_gender",
            "patient_photo",
        )
        read_only_fields = ("id", "status", "total_amount", "created_at", "reports")

    def validate(self, attrs):
        # Only on create: an admin PATCHing a status sends neither list, and
        # refusing that would make every status change a 400.
        if self.instance is None and not attrs.get("tests") and not attrs.get("packages"):
            raise serializers.ValidationError(
                "Choose at least one test or package to book."
            )
        return attrs

    def create(self, validated_data):
        tests = validated_data.pop("tests", [])
        packages = validated_data.pop("packages", [])
        validated_data["patient"] = self.context["request"].user
        center = validated_data.get("center")
        if center is not None:
            validated_data["kind"] = _booking_kind_for_provider(center)
        booking = DiagnosticBooking.objects.create(**validated_data)

        # A package is charged at its own price, not the sum of its parts —
        # that discount is the whole point of selling one. Its tests are still
        # written onto the booking so the lab knows what to run, and are not
        # charged twice if the patient also picked one of them separately.
        package_tests = [test for package in packages for test in package.tests.all()]
        booked_tests = {test.id: test for test in [*tests, *package_tests]}
        priced_separately = [
            test for test in tests if test.id not in {t.id for t in package_tests}
        ]

        if packages:
            booking.packages.set(packages)
        if booked_tests:
            booking.tests.set(booked_tests.values())

        booking.total_amount = sum(
            (test.price for test in priced_separately), Decimal("0")
        ) + sum((package.price for package in packages), Decimal("0"))
        booking.save(update_fields=["total_amount"])
        return booking


class MedicineReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicineReminder
        fields = (
            "id",
            "medicine",
            "medicine_name",
            "dosage",
            "reminder_times",
            "is_active",
            "start_date",
            "end_date",
            "created_at",
        )
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class WishlistSerializer(serializers.ModelSerializer):
    medicine_name = serializers.CharField(source="medicine.name", read_only=True)
    medicine_price = serializers.DecimalField(
        source="medicine.price",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = WishlistItem
        fields = ("id", "medicine", "medicine_name", "medicine_price", "created_at")
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class SupportTicketSerializer(serializers.ModelSerializer):
    """
    A ticket as the reporter sees it.

    ``priority`` is read-only here on purpose: it is triage, and a reporter
    who could set their own would set every ticket to High. The admin
    viewset swaps in a serializer that can write it.
    """

    reporter_name = serializers.CharField(source="user.name", read_only=True)
    reporter_phone = serializers.CharField(source="user.phone", read_only=True)

    class Meta:
        model = SupportTicket
        fields = (
            "id",
            "subject",
            "description",
            "status",
            "priority",
            "attachments",
            "about_provider",
            "assigned_to",
            "reporter_name",
            "reporter_phone",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "status",
            "priority",
            "assigned_to",
            "reporter_name",
            "reporter_phone",
            "created_at",
            "updated_at",
        )

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class ContentPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContentPage
        fields = ("id", "page_type", "title", "body", "is_published", "sort_order")


class DoctorAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = DoctorAvailability
        fields = (
            "id",
            "day_of_week",
            "start_time",
            "end_time",
            "consultation_mode",
            "is_active",
        )


class ProviderDoctorProfileSerializer(serializers.ModelSerializer):
    provider_name = serializers.CharField(source="provider.name", read_only=True)

    class Meta:
        model = DoctorProfile
        fields = (
            "id",
            "provider_name",
            "biography",
            "qualifications",
            "experience_years",
            "languages",
            "fee_online",
            "fee_in_clinic",
            "fee_home_visit",
            "consultation_modes",
            "clinic_name",
            "clinic_address",
            "clinic_city",
            "is_online",
            "auto_accept_appointments",
            "verification_status",
            "is_verified",
        )
        read_only_fields = ("verification_status", "is_verified")


class AdminDoctorSerializer(serializers.ModelSerializer):
    provider_id = serializers.IntegerField(source="provider.id", read_only=True)
    name = serializers.CharField(source="provider.name", read_only=True)
    phone = serializers.CharField(source="provider.user.phone", read_only=True)
    email = serializers.CharField(source="provider.user.email", read_only=True)
    specialty = serializers.CharField(source="provider.specialty", read_only=True)
    last_active_at = serializers.DateTimeField(
        source="provider.user.last_active_at",
        read_only=True,
    )
    # Annotated by the list view. Counting per row in the serializer would be
    # one query per doctor, which is what the annotation exists to avoid.
    consultation_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = DoctorProfile
        fields = (
            "id",
            "provider_id",
            "name",
            "phone",
            "email",
            "specialty",
            "profile_picture",
            "verification_status",
            "is_verified",
            "is_online",
            "experience_years",
            "clinic_name",
            "clinic_city",
            "rating_avg",
            "review_count",
            "consultation_count",
            "last_active_at",
            "created_at",
        )


class AdminPatientSerializer(serializers.ModelSerializer):
    appointment_count = serializers.IntegerField(read_only=True)
    order_count = serializers.IntegerField(read_only=True)
    city = serializers.SerializerMethodField()
    address = serializers.SerializerMethodField()

    def get_city(self, obj):
        address = obj.address
        return address.city if address else ""

    def get_address(self, obj):
        """The default address as one line, the way every profile prints it."""
        address = obj.address
        if not address:
            return ""
        parts = [
            address.address_line1,
            address.address_line2,
            address.landmark,
            address.city,
            address.state,
            address.postal_code,
        ]
        return ", ".join(str(part) for part in parts if part)

    class Meta:
        model = User
        fields = (
            "id",
            "name",
            "phone",
            "email",
            "dob",
            "is_active",
            "device",
            "source",
            "last_active_at",
            "city",
            "address",
            "appointment_count",
            "order_count",
            "date_joined",
        )


def _doctor_profile_for_provider(user):
    if not is_provider(user):
        return None
    return DoctorProfile.objects.filter(provider__user=user).first()


def _appointment_notification_data(appointment):
    return {"appointment_id": appointment.pk}


def _diagnostic_booking_notification_data(booking):
    return {"diagnostic_booking_id": booking.pk}


def _format_appointment_datetime(appointment):
    local = timezone.localtime(appointment.scheduled_at)
    return local.strftime("%d %b %Y, %I:%M %p")


def _notify_appointment_booked(appointment):
    doctor_name = appointment.doctor.provider.name
    when = _format_appointment_datetime(appointment)
    notify(
        appointment.patient,
        NotificationType.APPOINTMENT_BOOKED,
        "Appointment booked",
        f"Your appointment with Dr. {doctor_name} on {when} is pending confirmation.",
        data=_appointment_notification_data(appointment),
    )
    provider_user = appointment.doctor.provider.user
    if provider_user:
        notify(
            provider_user,
            NotificationType.APPOINTMENT_BOOKED,
            "New appointment request",
            f"{appointment.patient.name} requested an appointment on {when}.",
            data=_appointment_notification_data(appointment),
        )


def _notify_appointment_status_change(appointment, new_status):
    doctor_name = appointment.doctor.provider.name
    when = _format_appointment_datetime(appointment)
    mapping = {
        AppointmentStatus.CONFIRMED: (
            NotificationType.APPOINTMENT_CONFIRMED,
            "Appointment confirmed",
            f"Your appointment with Dr. {doctor_name} on {when} has been confirmed.",
        ),
        AppointmentStatus.REJECTED: (
            NotificationType.APPOINTMENT_REJECTED,
            "Appointment declined",
            f"Your appointment request with Dr. {doctor_name} on {when} was declined.",
        ),
        AppointmentStatus.COMPLETED: (
            NotificationType.APPOINTMENT_COMPLETED,
            "Appointment completed",
            f"Your appointment with Dr. {doctor_name} on {when} is marked completed.",
        ),
        AppointmentStatus.CANCELLED: (
            NotificationType.APPOINTMENT_CANCELLED,
            "Appointment cancelled",
            f"Your appointment with Dr. {doctor_name} on {when} was cancelled.",
        ),
    }
    entry = mapping.get(new_status)
    if not entry:
        return
    ntype, title, body = entry
    notify(
        appointment.patient,
        ntype,
        title,
        body,
        data=_appointment_notification_data(appointment),
    )


def _booking_kind_for_provider(center) -> str:
    """Lab / imaging / home-care bookings follow the centre's provider type."""
    mapping = {
        ProviderType.LAB.value: DiagnosticBookingKind.LAB,
        ProviderType.DIAGNOSTIC_CENTER.value: DiagnosticBookingKind.DIAGNOSTIC,
        ProviderType.HOME_HEALTHCARE.value: DiagnosticBookingKind.HOME_HEALTHCARE,
    }
    return mapping.get(getattr(center, "provider_type", ""), DiagnosticBookingKind.LAB)


def _notify_diagnostic_booking_requested(booking):
    notify(
        booking.patient,
        NotificationType.DIAG_BOOKING_REQUESTED,
        "Booking received",
        "Your diagnostic test booking has been received and is pending confirmation.",
        data=_diagnostic_booking_notification_data(booking),
    )


def _notify_diagnostic_booking_status_change(booking, new_status):
    mapping = {
        DiagnosticBookingStatus.CONFIRMED: (
            NotificationType.DIAG_BOOKING_CONFIRMED,
            "Booking confirmed",
            "Your diagnostic test booking has been confirmed.",
        ),
        DiagnosticBookingStatus.SLOT_ALLOTTED: (
            NotificationType.DIAG_BOOKING_CONFIRMED,
            "Slot allotted",
            "A slot has been allotted for your diagnostic test.",
        ),
        DiagnosticBookingStatus.ASSIGNED: (
            NotificationType.DIAG_BOOKING_CONFIRMED,
            "Visit assigned",
            "A clinician has been assigned for your home healthcare visit.",
        ),
        DiagnosticBookingStatus.SAMPLE_COLLECTED: (
            NotificationType.DIAG_SAMPLE_COLLECTED,
            "Sample collected",
            "Your diagnostic test sample has been collected.",
        ),
        DiagnosticBookingStatus.PATIENT_ARRIVED: (
            NotificationType.DIAG_SAMPLE_COLLECTED,
            "Checked in",
            "You have been checked in for your diagnostic test.",
        ),
        DiagnosticBookingStatus.IN_PROGRESS: (
            NotificationType.DIAG_SAMPLE_COLLECTED,
            "Visit in progress",
            "Your home healthcare visit is in progress.",
        ),
        DiagnosticBookingStatus.TEST_DONE: (
            NotificationType.DIAG_SAMPLE_COLLECTED,
            "Test done",
            "Your diagnostic test has been completed. The report will follow.",
        ),
        DiagnosticBookingStatus.REPORT_GENERATED: (
            NotificationType.DIAG_BOOKING_COMPLETED,
            "Report ready",
            "Your diagnostic test report is ready.",
        ),
        DiagnosticBookingStatus.COMPLETED: (
            NotificationType.DIAG_BOOKING_COMPLETED,
            "Test completed",
            "Your diagnostic test results are ready.",
        ),
        DiagnosticBookingStatus.CANCELLED: (
            NotificationType.DIAG_BOOKING_CANCELLED,
            "Booking cancelled",
            "Your diagnostic test booking has been cancelled.",
        ),
    }
    entry = mapping.get(new_status)
    if not entry:
        return
    ntype, title, body = entry
    notify(
        booking.patient,
        ntype,
        title,
        body,
        data=_diagnostic_booking_notification_data(booking),
    )


class DoctorListAPIView(ListAPIView):
    serializer_class = DoctorListSerializer
    pagination_class = GenericPaginationClass
    permission_classes = [ReadOnlyOrAdmin]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    search_fields = [
        "provider__name",
        "provider__specialty",
        "clinic_city",
        "biography",
    ]
    ordering_fields = ["rating_avg", "fee_online", "experience_years", "created_at"]

    def get_queryset(self):
        qs = (
            DoctorProfile.objects.filter(
                is_verified=True,
                verification_status=VerificationStatus.APPROVED,
            )
            .select_related("provider")
            .prefetch_related("specialties")
        )
        specialty = self.request.query_params.get("specialty")
        city = self.request.query_params.get("city")
        if specialty:
            qs = qs.filter(specialties__id=specialty)
        if city:
            qs = qs.filter(clinic_city__icontains=city)
        return qs.distinct()


class DoctorDetailAPIView(APIView):
    permission_classes = [ReadOnlyOrAdmin]

    def get(self, request, provider_id: int):
        qs = DoctorProfile.objects.select_related("provider").prefetch_related(
            "specialties",
            "availability_slots",
        )
        if not (request.user.is_authenticated and request.user.is_staff):
            qs = qs.filter(is_verified=True)
        doctor = get_object_or_404(qs, provider_id=provider_id)
        data = DoctorDetailSerializer(doctor).data
        data["availability"] = DoctorAvailabilitySerializer(
            doctor.availability_slots.filter(is_active=True),
            many=True,
        ).data
        data["is_saved"] = False
        if request.user.is_authenticated:
            data["is_saved"] = SavedDoctor.objects.filter(
                user=request.user,
                doctor=doctor,
            ).exists()
        return Response(data)


class SavedDoctorAPIView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        saved = SavedDoctor.objects.filter(user=request.user).select_related(
            "doctor__provider"
        )
        return Response(DoctorListSerializer([s.doctor for s in saved], many=True).data)

    def post(self, request):
        doctor_id = request.data.get("doctor_id")
        if not doctor_id:
            return Response(
                {"detail": "doctor_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        doctor = get_object_or_404(DoctorProfile, pk=doctor_id)
        SavedDoctor.objects.get_or_create(user=request.user, doctor=doctor)
        return Response({"detail": "Doctor saved"}, status=status.HTTP_201_CREATED)

    def delete(self, request):
        doctor_id = request.data.get("doctor_id") or request.query_params.get(
            "doctor_id"
        )
        if not doctor_id:
            return Response(
                {"detail": "doctor_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        deleted, _ = SavedDoctor.objects.filter(
            user=request.user, doctor_id=doctor_id
        ).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class QualificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Qualification
        fields = ("id", "name", "level", "is_active")


class QualificationViewSet(viewsets.ModelViewSet):
    """Option source for the doctor form's qualifications picker."""

    queryset = Qualification.objects.all()
    serializer_class = QualificationSerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["is_active", "level"]


class MedicalSpecialtyViewSet(viewsets.ModelViewSet):
    queryset = MedicalSpecialty.objects.all()
    serializer_class = MedicalSpecialtySerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["is_active", "name"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        return _visible_to(super().get_queryset(), self.request)


class AppointmentViewSet(viewsets.ModelViewSet):
    pagination_class = GenericPaginationClass
    # `patient` lets the admin dashboard show one patient's consultation
    # history without a bespoke endpoint; `doctor` does the same for the
    # doctor profile page, which reports that doctor's own consultation
    # counts and reviews.
    filterset_fields = ["status", "consultation_mode", "patient", "doctor"]
    ordering_fields = [
        "id",
        "scheduled_at",
        "created_at",
        "status",
        "consultation_mode",
        "patient__name",
        "doctor__provider__name",
        "doctor__clinic_city",
    ]
    # SearchFilter is a default backend, but without search_fields it is a
    # no-op: `?search=` was silently ignored and returned the whole list.
    search_fields = [
        "patient__name",
        "patient__phone",
        "doctor__provider__name",
        "doctor__clinic_city",
    ]

    def perform_create(self, serializer):
        appointment = serializer.save()
        _notify_appointment_booked(appointment)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            AppointmentSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def get_permissions(self):
        # Staff get the whole surface, not just reads. Support agents have to
        # reschedule, annotate and cancel on a patient's behalf — and until
        # this covered every action, the dashboard's "Cancel appointment"
        # button called an endpoint that answered 403 for the only role that
        # could see the button.
        if self.request.user.is_staff:
            return [IsAdmin()]
        return [IsCustomer()]

    def get_queryset(self):
        qs = Appointment.objects.select_related(
            "patient",
            "doctor__provider__user",
        )
        # The queue's tab strip is a group of statuses, not one status. See
        # `masters/buckets.py` for why this cannot be a `filterset_field`.
        qs = apply_bucket(
            qs,
            self.request.query_params.get("bucket"),
            APPOINTMENT_BUCKETS,
        )
        if self.request.user.is_staff:
            return qs
        return qs.filter(patient=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return AppointmentCreateSerializer
        # Support agents move appointments between states on the phone; the
        # patient-facing serializer locks `status` so a customer cannot mark
        # their own consultation completed.
        if self.request.user.is_staff:
            return AdminAppointmentSerializer
        return AppointmentSerializer

    @action(detail=True, methods=["post"])
    def checkout(self, request, pk=None):
        appointment = self.get_object()
        if appointment.consultation_mode != ConsultationMode.ONLINE:
            return Response(
                {"detail": "Checkout only for online consultations."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if appointment.payment_status == AppointmentPaymentStatus.PAID:
            return Response(
                {"detail": "Already paid."}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            razorpay_order = create_appointment_checkout(appointment)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "appointment": AppointmentSerializer(appointment).data,
                "razorpay_order": {
                    "id": razorpay_order.get("id"),
                    "amount": razorpay_order.get("amount"),
                    "currency": razorpay_order.get("currency"),
                },
            }
        )

    @action(detail=True, methods=["post"], url_path="confirm-payment")
    def confirm_payment(self, request, pk=None):
        appointment = self.get_object()
        order_id = request.data.get("razorpay_order_id")
        payment_id = request.data.get("razorpay_payment_id")
        signature = request.data.get("razorpay_signature")
        if not order_id or not payment_id:
            return Response(
                {"detail": "Missing payment fields."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            confirm_appointment_payment(
                appointment, order_id, payment_id, signature, request.data
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        appointment.refresh_from_db()
        return Response(
            AppointmentSerializer(appointment, context={"request": request}).data
        )

    @action(detail=True, methods=["post"], url_path="video-token")
    def video_token(self, request, pk=None):
        appointment = self.get_object()
        if not patient_can_join_video(appointment):
            return Response(
                {"detail": "Video call not available yet."},
                status=status.HTTP_403_FORBIDDEN,
            )
        client = __import__(
            "docatho_backend.healthcare.hms", fromlist=["HMSClient"]
        ).HMSClient()
        payload = mint_video_token(
            appointment, user=request.user, role=client.patient_role
        )
        return Response(payload)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        appointment = self.get_object()
        if appointment.status in (
            AppointmentStatus.COMPLETED,
            AppointmentStatus.CANCELLED,
        ):
            return Response(
                {"detail": "Cannot cancel."}, status=status.HTTP_400_BAD_REQUEST
            )
        appointment.status = AppointmentStatus.CANCELLED
        appointment.save(update_fields=["status", "updated_at"])
        return Response(AppointmentSerializer(appointment).data)

    @action(detail=True, methods=["post"])
    def rate(self, request, pk=None):
        appointment = self.get_object()
        if appointment.status != AppointmentStatus.COMPLETED:
            return Response(
                {"detail": "Only completed appointments can be rated."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rating = request.data.get("rating")
        if not rating or not (1 <= int(rating) <= 5):
            return Response(
                {"detail": "rating must be 1-5."}, status=status.HTTP_400_BAD_REQUEST
            )
        appointment.patient_rating = int(rating)
        appointment.patient_feedback = request.data.get("feedback", "")
        appointment.save(
            update_fields=["patient_rating", "patient_feedback", "updated_at"]
        )
        from django.db.models import Avg

        stats = Appointment.objects.filter(
            doctor=appointment.doctor,
            patient_rating__isnull=False,
        ).aggregate(avg=Avg("patient_rating"), cnt=Count("id"))
        appointment.doctor.rating_avg = stats["avg"] or 0
        appointment.doctor.review_count = stats["cnt"]
        appointment.doctor.save(
            update_fields=["rating_avg", "review_count", "updated_at"]
        )
        return Response(AppointmentSerializer(appointment).data)


def _visible_to(queryset, request):
    """Hide deactivated rows from patients; show them all to staff.

    These querysets were hardcoded to ``is_active=True``, so an admin who
    unchecked "Bookable by patients" watched the row vanish from the dashboard
    with no filter and no URL that could bring it back — and every subsequent
    PATCH 404'd, because the object was outside the viewset's own queryset.
    Recovery meant Django admin or SQL. ``MedicineViewset`` never had the bug,
    which is why the Medicines page's Active/Inactive filter works.
    """
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_staff:
        return queryset
    return queryset.filter(is_active=True)


class DiagnosticTestCategoryViewSet(viewsets.ModelViewSet):
    queryset = DiagnosticTestCategory.objects.all()
    serializer_class = DiagnosticTestCategorySerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["is_active", "name"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        return _visible_to(super().get_queryset(), self.request)


class DiagnosticTestViewSet(viewsets.ModelViewSet):
    queryset = DiagnosticTest.objects.all().select_related("category", "provider")
    serializer_class = DiagnosticTestSerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["category", "is_active", "provider", "test_kind"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "price", "mrp", "created_at"]

    def get_queryset(self):
        return _visible_to(super().get_queryset(), self.request)


class DiagnosticPackageViewSet(viewsets.ModelViewSet):
    queryset = DiagnosticPackage.objects.all().select_related("provider").prefetch_related("tests")
    serializer_class = DiagnosticPackageSerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["is_active", "provider"]
    search_fields = ["name", "description", "tests__name"]
    ordering_fields = ["name", "price", "created_at"]

    def get_queryset(self):
        return _visible_to(super().get_queryset(), self.request)


class DiagnosticBookingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsCustomer]
    serializer_class = DiagnosticBookingSerializer
    pagination_class = GenericPaginationClass
    filterset_fields = ["status", "kind"]
    ordering_fields = ["created_at", "scheduled_date"]

    def perform_create(self, serializer):
        booking = serializer.save()
        _notify_diagnostic_booking_requested(booking)

    def get_queryset(self):
        return DiagnosticBooking.objects.filter(
            patient=self.request.user
        ).prefetch_related("tests", "packages")


class MedicineReminderViewSet(viewsets.ModelViewSet):
    serializer_class = MedicineReminderSerializer
    permission_classes = [IsCustomer]
    pagination_class = GenericPaginationClass
    filterset_fields = ["is_active"]

    def get_queryset(self):
        return MedicineReminder.objects.filter(user=self.request.user)


class WishlistViewSet(viewsets.ModelViewSet):
    serializer_class = WishlistSerializer
    permission_classes = [IsCustomer]
    pagination_class = GenericPaginationClass

    def get_queryset(self):
        return (
            WishlistItem.objects.filter(user=self.request.user)
            .select_related("medicine")
            .order_by("-created_at")
        )


class AdminSupportTicketSerializer(SupportTicketSerializer):
    """
    Triage fields the reporter is not allowed to set.

    Priority and assignment are decisions the support desk makes; leaving them
    read-only for everyone meant the dashboard's Priority select wrote nothing
    and silently reverted on the next fetch.
    """

    class Meta(SupportTicketSerializer.Meta):
        read_only_fields = (
            "id",
            "reporter_name",
            "reporter_phone",
            "created_at",
            "updated_at",
        )


class SupportTicketViewSet(viewsets.ModelViewSet):
    serializer_class = SupportTicketSerializer
    pagination_class = GenericPaginationClass
    filterset_fields = ["status", "priority", "about_provider"]
    # Admins triage by subject or by who raised the ticket; customers only
    # ever search their own queryset, so the reporter fields are safe here.
    search_fields = ["subject", "description", "user__name", "user__phone"]
    ordering_fields = ["created_at", "status", "priority", "id"]

    def get_serializer_class(self):
        if self.request.user.is_authenticated and self.request.user.is_staff:
            return AdminSupportTicketSerializer
        return SupportTicketSerializer

    def get_permissions(self):
        # Staff raise tickets too — the dashboard's "Raise a ticket" button.
        # Without this, `create` fell through to IsCustomer and an admin got a
        # 403 from the one screen that offers the action.
        if self.request.user.is_staff:
            return [IsAdmin()]
        if self.action in ("create", "list", "retrieve"):
            return [IsCustomer()]
        return [IsAdmin()]

    def get_queryset(self):
        queryset = (
            SupportTicket.objects.select_related("user", "about_provider").all()
            if self.request.user.is_staff
            else SupportTicket.objects.filter(user=self.request.user)
        )
        # "In Progress" is open *and* in progress; see `masters/buckets.py`.
        return apply_bucket(
            queryset,
            self.request.query_params.get("bucket"),
            TICKET_BUCKETS,
        )


class ContentPageViewSet(viewsets.ModelViewSet):
    serializer_class = ContentPageSerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["page_type", "is_published"]
    search_fields = ["title", "body"]
    ordering_fields = ["title", "sort_order", "updated_at"]

    def get_queryset(self):
        qs = ContentPage.objects.all()
        if not self.request.user.is_staff:
            qs = qs.filter(is_published=True)
        return qs


class AIChatAPIView(APIView):
    permission_classes = [IsCustomer]

    def post(self, request):
        message = request.data.get("message", "")
        session_id = request.data.get("session_id")
        history = request.data.get("history", [])
        if session_id:
            session = get_object_or_404(AIChatSession, pk=session_id, user=request.user)
        else:
            session = AIChatSession.objects.create(
                user=request.user,
                title=(message[:50] if message else "Health chat"),
            )
        AIChatMessage.objects.create(session=session, role="user", content=message)
        ai = HealthcareAIService()
        result = ai.chat(message, history)
        AIChatMessage.objects.create(
            session=session,
            role="assistant",
            content=result.content,
            metadata={"source": result.source, **result.metadata},
        )
        return Response(
            {
                "session_id": session.id,
                "reply": result.content,
                "source": result.source,
                "metadata": result.metadata,
            }
        )


class AIPrescriptionAnalysisAPIView(APIView):
    permission_classes = [IsCustomer]

    def post(self, request):
        ai = HealthcareAIService()
        result = ai.analyze_prescription(
            request.data.get("text", ""),
            request.data.get("image_hint", ""),
        )
        return Response(
            {
                "analysis": result.content,
                "source": result.source,
                "metadata": result.metadata,
            }
        )


class ProviderDoctorProfileAPIView(APIView):
    permission_classes = [IsProvider]

    def get(self, request):
        profile = _doctor_profile_for_provider(request.user)
        if not profile:
            return Response(
                {"detail": "No doctor profile."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(ProviderDoctorProfileSerializer(profile).data)

    def patch(self, request):
        profile = _doctor_profile_for_provider(request.user)
        if not profile:
            return Response(
                {"detail": "No doctor profile."}, status=status.HTTP_404_NOT_FOUND
            )
        serializer = ProviderDoctorProfileSerializer(
            profile, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ProviderAvailabilityAPIView(APIView):
    permission_classes = [IsProvider]

    def get(self, request):
        profile = _doctor_profile_for_provider(request.user)
        if not profile:
            return Response(status=status.HTTP_404_NOT_FOUND)
        slots = profile.availability_slots.filter(is_active=True)
        return Response(
            {
                "availability": DoctorAvailabilitySerializer(slots, many=True).data,
                "blocked_dates": [
                    {"date": b.date, "reason": b.reason}
                    for b in profile.blocked_dates.all()
                ],
            }
        )

    def post(self, request):
        profile = _doctor_profile_for_provider(request.user)
        if not profile:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = DoctorAvailabilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        slot = serializer.save(doctor=profile)
        return Response(
            DoctorAvailabilitySerializer(slot).data, status=status.HTTP_201_CREATED
        )

    def patch(self, request):
        profile = _doctor_profile_for_provider(request.user)
        if not profile:
            return Response(status=status.HTTP_404_NOT_FOUND)
        slot = get_object_or_404(profile.availability_slots, pk=request.data.get("id"))
        serializer = DoctorAvailabilitySerializer(slot, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ProviderAppointmentListAPIView(APIView):
    permission_classes = [IsProvider]

    def get(self, request):
        profile = _doctor_profile_for_provider(request.user)
        if not profile:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Settle anything whose answer deadline has passed before answering, so
        # the queue never shows a request the platform has already accepted.
        auto_accept_overdue()
        qs = Appointment.objects.filter(doctor=profile).select_related(
            "patient", "doctor__provider"
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response(
            AppointmentSerializer(qs, many=True, context={"request": request}).data
        )

    def patch(self, request):
        profile = _doctor_profile_for_provider(request.user)
        if not profile:
            return Response(status=status.HTTP_404_NOT_FOUND)
        appointment = get_object_or_404(
            Appointment, pk=request.data.get("appointment_id"), doctor=profile
        )
        new_status = request.data.get("status")
        allowed = {
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.REJECTED,
            AppointmentStatus.IN_PROGRESS,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.CANCELLED,
        }
        if new_status not in allowed:
            return Response(
                {"detail": "Invalid status"}, status=status.HTTP_400_BAD_REQUEST
            )
        appointment.status = new_status
        if "prescription_notes" in request.data:
            appointment.prescription_notes = request.data["prescription_notes"]
        update_fields = ["status", "updated_at"]
        if "prescription_notes" in request.data:
            update_fields.append("prescription_notes")
        if new_status == AppointmentStatus.COMPLETED:
            appointment.completed_at = timezone.now()
            update_fields.append("completed_at")
        appointment.save(update_fields=update_fields)
        _notify_appointment_status_change(appointment, new_status)
        return Response(AppointmentSerializer(appointment).data)


class ProviderAppointmentDetailAPIView(APIView):
    """One appointment, with the patient history the detail screen shows.

    The app used to carry every field across as navigation params, which meant
    the detail screen showed whatever the list happened to have fetched and
    could not show anything the list did not — the payment split and the
    patient's previous consultations among them.
    """

    permission_classes = [IsProvider]

    def get(self, request, appointment_id: int):
        profile = _doctor_profile_for_provider(request.user)
        if not profile:
            return Response(status=status.HTTP_404_NOT_FOUND)
        appointment = get_object_or_404(
            Appointment.objects.select_related("patient", "doctor__provider"),
            pk=appointment_id,
            doctor=profile,
        )
        return Response(
            AppointmentSerializer(
                appointment,
                context={"request": request, "with_history": True},
            ).data,
        )


def _center_for_provider(user):
    """The lab / diagnostic centre the signed-in partner runs, if any.

    Bookings are attached to a `Provider`, not to a `DoctorProfile`, so this is
    deliberately not `_doctor_profile_for_provider`: a centre has no doctor
    profile and would have matched nothing.
    """
    if not is_provider(user):
        return None
    return Provider.objects.filter(user=user).first()


#: What a centre is allowed to move a booking to. Deliberately narrower than
#: `DiagnosticBookingStatus`: only an admin cancels on a patient's behalf, and
#: `requested` is where a booking starts — nothing may move back into it.
CENTER_ALLOWED_BOOKING_STATUSES = frozenset(
    {
        DiagnosticBookingStatus.CONFIRMED,
        DiagnosticBookingStatus.SLOT_ALLOTTED,
        DiagnosticBookingStatus.PATIENT_ARRIVED,
        DiagnosticBookingStatus.SAMPLE_COLLECTED,
        DiagnosticBookingStatus.TEST_DONE,
        DiagnosticBookingStatus.IN_PROGRESS,
        DiagnosticBookingStatus.REPORT_GENERATED,
        DiagnosticBookingStatus.COMPLETED,
        DiagnosticBookingStatus.CANCELLED,
    },
)


class ProviderDiagnosticBookingListAPIView(APIView):
    """The centre's own booking queue, newest slot first.

    The centre app had no endpoint at all before this: the only booking APIs
    were the patient's own list and the admin's, neither of which a partner
    login can reach.
    """

    permission_classes = [IsProvider]

    def get(self, request):
        center = _center_for_provider(request.user)
        if center is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        auto_accept_overdue()
        qs = (
            DiagnosticBooking.objects.filter(center=center)
            .select_related("patient", "center")
            .prefetch_related("tests__category", "packages")
            .order_by("-scheduled_date", "-scheduled_time", "-created_at")
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        visit_type = request.query_params.get("visit_type")
        if visit_type:
            qs = qs.filter(visit_type=visit_type)
        return Response(
            DiagnosticBookingSerializer(
                qs,
                many=True,
                context={"request": request},
            ).data,
        )


class ProviderDiagnosticBookingDetailAPIView(APIView):
    """Read one booking, move its status, or attach its reports."""

    permission_classes = [IsProvider]

    def _booking_or_404(self, request, booking_id: int):
        center = _center_for_provider(request.user)
        if center is None:
            return None
        return (
            DiagnosticBooking.objects.filter(pk=booking_id, center=center)
            .select_related("patient", "center")
            .prefetch_related("tests__category", "packages")
            .first()
        )

    def _serialized(self, request, booking):
        return Response(
            DiagnosticBookingSerializer(booking, context={"request": request}).data,
        )

    def get(self, request, booking_id: int):
        booking = self._booking_or_404(request, booking_id)
        if booking is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return self._serialized(request, booking)

    def patch(self, request, booking_id: int):
        booking = self._booking_or_404(request, booking_id)
        if booking is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        update_fields = ["updated_at"]

        new_status = request.data.get("status")
        if new_status is not None:
            if new_status not in CENTER_ALLOWED_BOOKING_STATUSES:
                return Response(
                    {"detail": f"Centres cannot set status '{new_status}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            booking.status = new_status
            update_fields.append("status")

        # Report URLs from /api/uploads/, appended rather than replaced: a
        # second upload is another test's result, not a correction of the first.
        reports = request.data.get("reports")
        if reports is not None:
            if not isinstance(reports, list) or not all(
                isinstance(url, str) and url.strip() for url in reports
            ):
                return Response(
                    {"detail": "`reports` must be a list of upload URLs."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            booking.reports = [*booking.reports, *reports]
            update_fields.append("reports")

        if len(update_fields) == 1:
            return Response(
                {"detail": "Send `status`, `reports`, or both."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        booking.save(update_fields=update_fields)
        return self._serialized(request, booking)


class ProviderAppointmentVideoTokenAPIView(APIView):
    permission_classes = [IsProvider]

    def post(self, request, appointment_id: int):
        profile = _doctor_profile_for_provider(request.user)
        if not profile:
            return Response(status=status.HTTP_404_NOT_FOUND)
        appointment = get_object_or_404(Appointment, pk=appointment_id, doctor=profile)
        if not provider_can_join_video(appointment):
            return Response(
                {"detail": "Video call not available yet."},
                status=status.HTTP_403_FORBIDDEN,
            )
        from docatho_backend.healthcare.hms import HMSClient

        client = HMSClient()
        payload = mint_video_token(
            appointment, user=request.user, role=client.doctor_role
        )
        return Response(payload)


class ConsultationMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.name", read_only=True)
    # Which side of the thread the bubble sits on. Derived rather than stored:
    # an admin posting on a doctor's behalf must still render as the doctor.
    is_from_patient = serializers.SerializerMethodField()

    def get_is_from_patient(self, obj):
        return obj.sender_id == obj.appointment.patient_id

    class Meta:
        model = ConsultationMessage
        fields = (
            "id",
            "kind",
            "body",
            "attachment_url",
            "attachment_name",
            "attachment_size",
            "reply_to",
            "sender",
            "sender_name",
            "is_from_patient",
            "read_at",
            "created_at",
        )
        read_only_fields = ("id", "sender", "sender_name", "is_from_patient", "created_at")

    def validate(self, attrs):
        kind = attrs.get("kind", ConsultationMessageKind.TEXT)
        body = (attrs.get("body") or "").strip()
        attachment = (attrs.get("attachment_url") or "").strip()
        # An empty text message is a blank bubble nobody can read; a file
        # message with no file is a download button that 404s.
        if kind == ConsultationMessageKind.TEXT and not body:
            raise serializers.ValidationError({"body": "Message cannot be empty."})
        if kind == ConsultationMessageKind.FILE and not attachment:
            raise serializers.ValidationError(
                {"attachment_url": "Attach a file or send this as a text message."},
            )
        return attrs


class ConsultationMessagesAPIView(APIView):
    """
    GET/POST the conversation attached to one appointment.

    Three roles can reach it and each sees only their own consultations: the
    patient it belongs to, the doctor taking it, and staff. Anyone else gets a
    404 rather than a 403, so the endpoint does not confirm that an
    appointment id exists to someone with no business knowing.
    """

    permission_classes = [permissions.IsAuthenticated]

    def _appointment_for(self, request, appointment_id: int) -> Appointment:
        appointment = get_object_or_404(
            Appointment.objects.select_related("patient", "doctor__provider__user"),
            pk=appointment_id,
        )
        user = request.user
        if user.is_staff:
            return appointment
        if appointment.patient_id == user.id:
            return appointment
        doctor_user_id = getattr(appointment.doctor.provider, "user_id", None)
        if doctor_user_id == user.id:
            return appointment
        raise Http404

    def get(self, request, appointment_id: int):
        appointment = self._appointment_for(request, appointment_id)
        messages = appointment.messages.select_related("sender").all()
        return Response(ConsultationMessageSerializer(messages, many=True).data)

    def post(self, request, appointment_id: int):
        appointment = self._appointment_for(request, appointment_id)
        serializer = ConsultationMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = serializer.save(appointment=appointment, sender=request.user)
        return Response(
            ConsultationMessageSerializer(message).data,
            status=status.HTTP_201_CREATED,
        )


class AdminDashboardStatsAPIView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response(build_dashboard_stats(request.query_params))


class AdminPatientListAPIView(ListAPIView):
    permission_classes = [IsAdmin]
    serializer_class = AdminPatientSerializer
    pagination_class = GenericPaginationClass
    # OrderingFilter is a project-wide default backend; naming the list here
    # dropped it, so `?ordering=` was silently ignored on this endpoint.
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_active", "device", "source"]
    search_fields = ["name", "phone", "email", "addresses__city"]
    ordering_fields = [
        "name",
        "date_joined",
        "last_active_at",
        "appointment_count",
        "order_count",
    ]

    def get_queryset(self):
        return (
            User.objects.filter(is_staff=False)
            .exclude(provider__isnull=False)
            .prefetch_related("addresses")
            .annotate(
                appointment_count=Count("appointments", distinct=True),
                order_count=Count("orders", distinct=True),
            )
            .order_by("-date_joined")
        )


class AdminDoctorListAPIView(ListAPIView):
    permission_classes = [IsAdmin]
    serializer_class = AdminDoctorSerializer
    pagination_class = GenericPaginationClass
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        "verification_status",
        "is_verified",
        "is_online",
        "clinic_city",
    ]
    search_fields = [
        "provider__name",
        "provider__specialty",
        "provider__user__phone",
        "clinic_name",
        "clinic_city",
    ]
    ordering_fields = [
        "provider__name",
        "provider__specialty",
        "clinic_city",
        "clinic_name",
        "experience_years",
        "rating_avg",
        "consultation_count",
        "is_online",
        "provider__user__last_active_at",
        "created_at",
    ]

    def get_queryset(self):
        return (
            DoctorProfile.objects.select_related("provider__user")
            .annotate(consultation_count=Count("appointments", distinct=True))
            .order_by("-created_at")
        )


class AdminDoctorVerificationAPIView(APIView):
    permission_classes = [IsAdmin]

    def patch(self, request, pk: int):
        doctor = get_object_or_404(DoctorProfile, pk=pk)
        action_name = request.data.get("action")
        if action_name == "approve":
            doctor.verification_status = VerificationStatus.APPROVED
            doctor.is_verified = True
        elif action_name == "reject":
            doctor.verification_status = VerificationStatus.REJECTED
            doctor.is_verified = False
        else:
            return Response(
                {"detail": "action must be approve or reject"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        doctor.save(update_fields=["verification_status", "is_verified", "updated_at"])
        return Response(AdminDoctorSerializer(doctor).data)


class AdminDoctorAvailabilityViewSet(viewsets.ModelViewSet):
    """Admin: manage any doctor's weekly slots.

    The provider app owns this for the doctor themselves; support had no way
    in at all, so "the clinic rang, they can't do Tuesdays any more" required
    a developer.
    """

    permission_classes = [IsAdmin]
    serializer_class = DoctorAvailabilitySerializer
    pagination_class = GenericPaginationClass
    queryset = DoctorAvailability.objects.select_related("doctor__provider")
    filterset_fields = ["doctor", "consultation_mode", "is_active"]
    ordering = ["day_of_week", "start_time"]

    def perform_create(self, serializer):
        doctor_id = self.request.data.get("doctor")
        doctor = get_object_or_404(DoctorProfile, pk=doctor_id)

        # `doctor` is injected here rather than declared on the serializer, so
        # DRF cannot build the UniqueTogetherValidator for the model's
        # unique_together and a repeat slot reached the database as an
        # IntegrityError — a 500 with a debug page in the body. The dashboard
        # guards this client-side, but only against the slot list it has
        # already loaded, so a stale drawer (or any other API client) could
        # still trigger it.
        data = serializer.validated_data
        clash = DoctorAvailability.objects.filter(
            doctor=doctor,
            day_of_week=data["day_of_week"],
            start_time=data["start_time"],
            consultation_mode=data.get(
                "consultation_mode", ConsultationMode.ONLINE
            ),
        ).exists()
        if clash:
            raise serializers.ValidationError(
                {"start_time": "This doctor already has a slot at that time."},
            )

        serializer.save(doctor=doctor)


class ProviderAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProviderAvailability
        fields = (
            "id",
            "day_of_week",
            "start_time",
            "end_time",
            "is_active",
        )


class AdminProviderAvailabilityViewSet(viewsets.ModelViewSet):
    """Admin: weekly hours for a lab, diagnostic centre or home-care agency."""

    permission_classes = [IsAdmin]
    serializer_class = ProviderAvailabilitySerializer
    pagination_class = GenericPaginationClass
    queryset = ProviderAvailability.objects.select_related("provider")
    filterset_fields = ["provider", "is_active"]
    ordering = ["day_of_week", "start_time"]

    def perform_create(self, serializer):
        provider_id = self.request.data.get("provider")
        provider = get_object_or_404(Provider, pk=provider_id)
        data = serializer.validated_data
        clash = ProviderAvailability.objects.filter(
            provider=provider,
            day_of_week=data["day_of_week"],
            start_time=data["start_time"],
        ).exists()
        if clash:
            raise serializers.ValidationError(
                {"start_time": "This centre already has a slot at that time."},
            )
        serializer.save(provider=provider)


class BlockedDateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlockedDate
        fields = ("id", "date", "reason")


class AdminBlockedDateViewSet(viewsets.ModelViewSet):
    """Admin: a doctor's days off.

    ``BlockedDate`` existed but nothing could write one — not the provider app,
    not the admin. Weekly slots therefore repeated forever, so a doctor on
    leave kept taking bookings for days they were away.
    """

    permission_classes = [IsAdmin]
    serializer_class = BlockedDateSerializer
    pagination_class = GenericPaginationClass
    queryset = BlockedDate.objects.select_related("doctor__provider")
    filterset_fields = ["doctor"]
    ordering = ["date"]

    def perform_create(self, serializer):
        doctor = get_object_or_404(
            DoctorProfile,
            pk=self.request.data.get("doctor"),
        )
        # `doctor` is injected rather than declared on the serializer, so DRF
        # cannot build the UniqueTogetherValidator for the model's
        # unique_together — a repeat date would reach the DB as an
        # IntegrityError, i.e. a 500 instead of a 400.
        if BlockedDate.objects.filter(
            doctor=doctor,
            date=serializer.validated_data["date"],
        ).exists():
            raise serializers.ValidationError(
                {"date": "This doctor is already blocked on that date."},
            )
        serializer.save(doctor=doctor)


class AdminPrescriptionSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.name", read_only=True)
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    image_url = serializers.SerializerMethodField()
    order_count = serializers.SerializerMethodField()
    orders = serializers.SerializerMethodField()
    reviewed_by_name = serializers.CharField(
        source="reviewed_by.name",
        read_only=True,
        default=None,
    )

    class Meta:
        model = Prescription
        fields = (
            "id",
            "user",
            "user_name",
            "user_phone",
            "image_url",
            "status",
            "notes",
            "order_count",
            "orders",
            "reviewed_by_name",
            "reviewed_at",
            "created_at",
        )
        read_only_fields = (
            "id",
            "user",
            "created_at",
            "reviewed_at",
            "reviewed_by_name",
        )

    def get_image_url(self, obj) -> str | None:
        if not obj.image:
            return None
        request = self.context.get("request")
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url

    def get_order_count(self, obj) -> int:
        return obj.orders.count()

    def get_orders(self, obj) -> list[dict]:
        """What this document is actually authorising.

        The reviewer used to see an image, a bare order count and nothing else
        — no line items and no drug schedule, which is the only fact that
        decides whether a prescription is required at all. Approving without
        them is approving a JPEG.
        """
        return [
            {
                "id": order.id,
                "order_number": order.order_number,
                "status": order.status,
                "total": str(order.total),
                "items": [
                    {
                        "id": item.id,
                        "name": item.medicine.name,
                        "schedule": item.medicine.schedule,
                        "quantity": item.quantity,
                        "prescription_required": item.prescription_required,
                    }
                    for item in order.items.all()
                ],
            }
            for order in obj.orders.all()
        ]


class AdminPrescriptionViewSet(viewsets.ModelViewSet):
    """Admin: the prescription review queue.

    Patients upload a prescription to clear the checkout gate on Schedule
    H/H1/X medicines, and ``Prescription.status`` defaults to *Pending
    review* — but nothing in the system ever set it to approved or rejected,
    and ``PrescriptionViewSet`` scopes its queryset to ``request.user``, so an
    admin listing prescriptions saw an empty list. Uploads had no reviewer.
    """

    permission_classes = [IsAdmin]
    serializer_class = AdminPrescriptionSerializer
    pagination_class = GenericPaginationClass
    queryset = Prescription.objects.select_related("user", "reviewed_by").prefetch_related(
        "orders__items__medicine",
    )
    filterset_fields = ["status", "user"]
    search_fields = ["user__name", "user__phone", "notes"]
    ordering_fields = ["created_at", "status"]
    # Documents are uploaded by the patient and are evidence; an admin
    # reviews them, never edits or destroys them.
    http_method_names = ["get", "patch", "head", "options"]

    def perform_update(self, serializer):
        """Stamp who decided, and when.

        Only on a real decision — a PATCH that leaves the status pending (an
        admin saving a note mid-review) must not claim the document was
        reviewed.
        """
        decided = serializer.validated_data.get("status", serializer.instance.status)
        if decided != Prescription.Status.PENDING:
            serializer.save(
                reviewed_by=self.request.user,
                reviewed_at=timezone.now(),
            )
        else:
            serializer.save()


class AdminDoctorProfileSerializer(serializers.ModelSerializer):
    """Admin-editable clinical profile for a doctor.

    Identity (name, phone, email) belongs to the linked ``Provider`` and is
    edited through the partners endpoint, so it is read-only here — one field,
    one owner.
    """

    provider_id = serializers.IntegerField(source="provider.id", read_only=True)
    name = serializers.CharField(source="provider.name", read_only=True)
    # Writable, unlike name: it is the doctor's contact number *and* their
    # login (`USERNAME_FIELD = "phone"`), so support needs to correct a typo
    # here rather than send the doctor away to re-register.
    phone = serializers.CharField(source="provider.user.phone", required=False)
    specialty = serializers.CharField(source="provider.specialty", read_only=True)
    specialties = MedicalSpecialtySerializer(many=True, read_only=True)

    def validate_phone(self, value):
        """Refuse a number another account already answers to.

        `User.phone` is the login field but carries no unique constraint
        (auth.W004). `AdminLoginView` does `User.objects.get(phone=...)`, so a
        duplicate does not merely confuse the directory — it makes that query
        raise MultipleObjectsReturned and locks *both* accounts out with a 500.
        """
        phone = (value or "").strip()
        if not phone:
            msg = "A phone number is required."
            raise serializers.ValidationError(msg)

        clash = User.objects.filter(phone=phone)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.provider.user_id)
        if clash.exists():
            msg = "Another account already uses this number."
            raise serializers.ValidationError(msg)
        return phone
    # The M2M was read-only in every serializer, so the dashboard's Specialties
    # manager wrote rows that nothing could ever attach to a doctor — and the
    # patient app's specialty browse returned an empty list forever.
    specialty_ids = serializers.PrimaryKeyRelatedField(
        source="specialties",
        queryset=MedicalSpecialty.objects.all(),
        many=True,
        required=False,
        write_only=True,
    )

    class Meta:
        model = DoctorProfile
        fields = (
            "id",
            "provider_id",
            "name",
            "phone",
            "specialty",
            "specialties",
            "specialty_ids",
            "biography",
            "qualifications",
            "qualifications_ug",
            "qualifications_pg",
            "experience_years",
            "languages",
            "fee_online",
            "fee_in_clinic",
            "fee_home_visit",
            "consultation_modes",
            "clinic_name",
            "clinic_address",
            "clinic_city",
            "clinic_latitude",
            "clinic_longitude",
            "clinic_images",
            "profile_picture",
            # Admin-writable: no endpoint ever accepted these as uploads, so
            # onboarding a doctor meant approving them with no documents at all.
            "license_document",
            "degree_document",
            "is_online",
            "auto_accept_appointments",
            "verification_status",
            "is_verified",
            "rating_avg",
            "review_count",
            "created_at",
        )
        read_only_fields = (
            "id",
            "rating_avg",
            "review_count",
            "created_at",
        )

    def update(self, instance, validated_data):
        """Keep `Provider.specialty` in step with the specialties M2M.

        The apps render `provider.specialty` — a free-text CharField — on the
        doctor card and detail header, while the browse filter matches on the
        M2M. They are edited on different screens, so a doctor filed under
        "Dermatologist" could display "Cardiologist" indefinitely. Deriving one
        from the other on save leaves a single place to get it wrong.
        """
        # `source="provider.user.phone"` nests into validated_data as
        # {"provider": {"user": {"phone": ...}}}, which ModelSerializer.update
        # cannot write — it would try to set a `provider` attribute on the
        # profile. Pull it out and save the owning row directly.
        provider_data = validated_data.pop("provider", {})
        phone = (provider_data.get("user") or {}).get("phone")
        if phone:
            user = instance.provider.user
            user.phone = phone
            user.save(update_fields=["phone"])

        if "qualifications_ug" in validated_data or "qualifications_pg" in validated_data:
            ug = validated_data.get("qualifications_ug", instance.qualifications_ug)
            pg = validated_data.get("qualifications_pg", instance.qualifications_pg)
            validated_data["qualifications"] = list(ug or []) + list(pg or [])

        doctor = super().update(instance, validated_data)
        if "specialties" in validated_data:
            names = list(doctor.specialties.values_list("name", flat=True))
            label = ", ".join(names)
            if label and doctor.provider.specialty != label:
                doctor.provider.specialty = label[:255]
                doctor.provider.save(update_fields=["specialty"])
        return doctor


class AdminDoctorDetailAPIView(RetrieveUpdateDestroyAPIView):
    """Admin: read, edit or remove one doctor's clinical profile."""

    permission_classes = [IsAdmin]
    serializer_class = AdminDoctorProfileSerializer
    queryset = DoctorProfile.objects.select_related("provider__user")

    def destroy(self, request, *args, **kwargs):
        doctor = self.get_object()
        # Appointment.doctor cascades. Deleting a doctor who has consulted
        # would silently erase those consultations — including completed ones
        # holding prescription notes. Refuse, and point at the reversible move.
        booked = Appointment.objects.filter(doctor=doctor).count()
        if booked:
            return Response(
                {
                    "detail": (
                        f"This doctor has {booked} appointment(s). Deleting them "
                        f"would erase that history. Set them offline or reject "
                        f"their verification instead."
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


class AdminPatientSerializerWritable(AdminPatientSerializer):
    """Same shape as the list, but the contactable fields accept writes."""

    class Meta(AdminPatientSerializer.Meta):
        read_only_fields = ("id", "phone", "date_joined")


class AdminPatientDetailAPIView(RetrieveUpdateDestroyAPIView):
    """Admin: read, correct or deactivate one patient."""

    permission_classes = [IsAdmin]
    serializer_class = AdminPatientSerializerWritable
    queryset = User.objects.filter(is_staff=False)

    def get_queryset(self):
        return User.objects.filter(is_staff=False).annotate(
            appointment_count=Count("appointments", distinct=True),
            order_count=Count("orders", distinct=True),
        )

    def destroy(self, request, *args, **kwargs):
        patient = self.get_object()
        # Orders, appointments and payment transactions all cascade off the
        # user. A patient with history is deactivated, never erased: the
        # records are clinical and financial, and "delete the customer" must
        # not quietly mean "delete the evidence".
        if patient.appointment_count or patient.order_count:
            if patient.is_active:
                patient.is_active = False
                patient.save(update_fields=["is_active"])
            return Response(
                {
                    "detail": "Patient deactivated. Their orders and consultations are kept.",
                    "deactivated": True,
                },
                status=status.HTTP_200_OK,
            )
        return super().destroy(request, *args, **kwargs)


class AdminDiagnosticBookingSerializer(DiagnosticBookingSerializer):
    class Meta(DiagnosticBookingSerializer.Meta):
        # Admins reschedule and re-price bookings; only the identity and the
        # audit timestamp are fixed.
        read_only_fields = ("id", "created_at")


class AdminDiagnosticBookingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdmin]
    serializer_class = AdminDiagnosticBookingSerializer
    pagination_class = GenericPaginationClass
    queryset = DiagnosticBooking.objects.prefetch_related(
        "tests", "packages"
    ).select_related("patient", "center")
    filterset_fields = [
        "status",
        "patient",
        "kind",
        "center",
        "visit_type",
        "center__city",
    ]
    # `tests__name` spans a many-to-many, so a booking with three matching
    # tests would be returned three times; DRF's SearchFilter detects that
    # and applies .distinct() for us.
    search_fields = [
        "patient__name",
        "patient__phone",
        "tests__name",
        "center__name",
        "center__city",
    ]
    ordering_fields = [
        "id",
        "created_at",
        "scheduled_date",
        "total_amount",
        "status",
        "visit_type",
        "center__name",
        "center__city",
        "patient__name",
    ]
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return apply_bucket(
            super().get_queryset(),
            self.request.query_params.get("bucket"),
            BOOKING_BUCKETS,
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        old_status = instance.status
        response = super().partial_update(request, *args, **kwargs)
        instance.refresh_from_db()
        if instance.status != old_status:
            _notify_diagnostic_booking_status_change(instance, instance.status)
        return response
