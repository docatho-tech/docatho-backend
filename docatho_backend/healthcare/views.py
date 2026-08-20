"""DRF views for Phase 1 healthcare: doctors, diagnostics, AI, admin."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Count
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
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
from docatho_backend.healthcare.video import mint_video_token
from docatho_backend.healthcare.video import patient_can_join_video
from docatho_backend.healthcare.video import provider_can_join_video
from docatho_backend.healthcare.models import AIChatMessage
from docatho_backend.healthcare.models import AIChatSession
from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPaymentStatus
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import ContentPage
from docatho_backend.healthcare.models import DiagnosticBooking
from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.healthcare.models import DiagnosticTest
from docatho_backend.healthcare.models import DiagnosticTestCategory
from docatho_backend.healthcare.models import DoctorAvailability
from docatho_backend.healthcare.models import DoctorProfile
from docatho_backend.healthcare.models import MedicalSpecialty
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
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import Prescription
from docatho_backend.orders.paginators import GenericPaginationClass
from docatho_backend.notifications.models import NotificationType
from docatho_backend.notifications.services import notify
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
    """

    class Meta(AppointmentSerializer.Meta):
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

    class Meta:
        model = DiagnosticTest
        fields = (
            "id",
            "name",
            "category",
            "category_name",
            "description",
            "price",
            "preparation_instructions",
            "is_active",
        )


class DiagnosticBookingSerializer(serializers.ModelSerializer):
    test_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=DiagnosticTest.objects.filter(is_active=True),
        source="tests",
        write_only=True,
    )
    tests = DiagnosticTestSerializer(many=True, read_only=True)
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    patient_phone = serializers.CharField(source="patient.phone", read_only=True)

    class Meta:
        model = DiagnosticBooking
        fields = (
            "id",
            "center",
            "tests",
            "test_ids",
            "status",
            "scheduled_date",
            "scheduled_time",
            "total_amount",
            "patient_address",
            "notes",
            "created_at",
            "patient_name",
            "patient_phone",
        )
        read_only_fields = ("id", "status", "total_amount", "created_at")

    def create(self, validated_data):
        tests = validated_data.pop("tests", [])
        validated_data["patient"] = self.context["request"].user
        booking = DiagnosticBooking.objects.create(**validated_data)
        if tests:
            booking.tests.set(tests)
            booking.total_amount = sum(t.price for t in tests)
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
    class Meta:
        model = SupportTicket
        fields = ("id", "subject", "description", "status", "assigned_to", "created_at")
        read_only_fields = ("id", "status", "assigned_to", "created_at")

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

    class Meta:
        model = DoctorProfile
        fields = (
            "id",
            "provider_id",
            "name",
            "phone",
            "verification_status",
            "is_verified",
            "experience_years",
            "clinic_city",
            "rating_avg",
            "review_count",
            "created_at",
        )


class AdminPatientSerializer(serializers.ModelSerializer):
    appointment_count = serializers.IntegerField(read_only=True)
    order_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "name",
            "phone",
            "email",
            "dob",
            "is_active",
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
        DiagnosticBookingStatus.SAMPLE_COLLECTED: (
            NotificationType.DIAG_SAMPLE_COLLECTED,
            "Sample collected",
            "Your diagnostic test sample has been collected.",
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


class MedicalSpecialtyViewSet(viewsets.ModelViewSet):
    queryset = MedicalSpecialty.objects.filter(is_active=True)
    serializer_class = MedicalSpecialtySerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["is_active", "name"]
    search_fields = ["name"]


class AppointmentViewSet(viewsets.ModelViewSet):
    pagination_class = GenericPaginationClass
    # `patient` lets the admin dashboard show one patient's consultation
    # history without a bespoke endpoint.
    filterset_fields = ["status", "consultation_mode", "patient"]
    ordering_fields = ["scheduled_at", "created_at"]
    # SearchFilter is a default backend, but without search_fields it is a
    # no-op: `?search=` was silently ignored and returned the whole list.
    search_fields = [
        "patient__name",
        "patient__phone",
        "doctor__provider__name",
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
        qs = Appointment.objects.select_related("patient", "doctor__provider")
        if self.request.user.is_staff:
            return qs.all()
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


class DiagnosticTestCategoryViewSet(viewsets.ModelViewSet):
    queryset = DiagnosticTestCategory.objects.filter(is_active=True)
    serializer_class = DiagnosticTestCategorySerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["is_active", "name"]
    search_fields = ["name"]


class DiagnosticTestViewSet(viewsets.ModelViewSet):
    queryset = DiagnosticTest.objects.filter(is_active=True).select_related("category")
    serializer_class = DiagnosticTestSerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["category", "is_active"]
    search_fields = ["name", "description"]


class DiagnosticBookingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsCustomer]
    serializer_class = DiagnosticBookingSerializer
    pagination_class = GenericPaginationClass
    filterset_fields = ["status"]
    ordering_fields = ["created_at", "scheduled_date"]

    def perform_create(self, serializer):
        booking = serializer.save()
        _notify_diagnostic_booking_requested(booking)

    def get_queryset(self):
        return DiagnosticBooking.objects.filter(
            patient=self.request.user
        ).prefetch_related("tests")


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


class SupportTicketViewSet(viewsets.ModelViewSet):
    serializer_class = SupportTicketSerializer
    pagination_class = GenericPaginationClass
    filterset_fields = ["status"]
    # Admins triage by subject or by who raised the ticket; customers only
    # ever search their own queryset, so the reporter fields are safe here.
    search_fields = ["subject", "description", "user__name", "user__phone"]

    def get_permissions(self):
        if self.action in ("list", "retrieve") and self.request.user.is_staff:
            return [IsAdmin()]
        if self.action in ("create", "list", "retrieve"):
            return [IsCustomer()]
        return [IsAdmin()]

    def get_queryset(self):
        if self.request.user.is_staff:
            return SupportTicket.objects.select_related("user").all()
        return SupportTicket.objects.filter(user=self.request.user)


class ContentPageViewSet(viewsets.ModelViewSet):
    serializer_class = ContentPageSerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = GenericPaginationClass
    filterset_fields = ["page_type", "is_published"]
    search_fields = ["title", "body"]

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


class AdminDashboardStatsAPIView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        today = timezone.localdate()
        patients = User.objects.filter(is_staff=False).exclude(provider__isnull=False)
        doctors = DoctorProfile.objects.all()
        revenue = Order.objects.filter(payment_status="paid").aggregate(
            total=Sum("total")
        )["total"] or Decimal("0")
        return Response(
            {
                "patients_count": patients.count(),
                "doctors_count": doctors.count(),
                "pending_doctor_verifications": doctors.filter(
                    verification_status=VerificationStatus.PENDING
                ).count(),
                "appointments_today": Appointment.objects.filter(
                    scheduled_at__date=today
                ).count(),
                "diagnostic_bookings_count": DiagnosticBooking.objects.count(),
                "diagnostic_bookings_requested": DiagnosticBooking.objects.filter(
                    status="requested"
                ).count(),
                "open_support_tickets": SupportTicket.objects.filter(
                    status="open"
                ).count(),
                # Uploads still waiting on a human. Surfaced because a review
                # queue nobody can see is a queue nobody works.
                "prescriptions_pending": Prescription.objects.filter(
                    status=Prescription.Status.PENDING,
                ).count(),
                "appointments_pending_payment": Appointment.objects.filter(
                    consultation_mode=ConsultationMode.ONLINE,
                    payment_status=AppointmentPaymentStatus.PENDING,
                    status__in=[AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED],
                ).count(),
                "pharma_orders_count": Order.objects.count(),
                "revenue_total": str(revenue),
                "appointments_by_status": dict(
                    Appointment.objects.values("status")
                    .annotate(count=Count("id"))
                    .values_list("status", "count"),
                ),
            }
        )


class AdminPatientListAPIView(ListAPIView):
    permission_classes = [IsAdmin]
    serializer_class = AdminPatientSerializer
    pagination_class = GenericPaginationClass
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["is_active"]
    search_fields = ["name", "phone", "email"]

    def get_queryset(self):
        return (
            User.objects.filter(is_staff=False)
            .exclude(provider__isnull=False)
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
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["verification_status", "is_verified"]
    search_fields = ["provider__name", "provider__user__phone", "clinic_city"]

    def get_queryset(self):
        return DoctorProfile.objects.select_related("provider__user").order_by(
            "-created_at"
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
        serializer.save(doctor=doctor)


class AdminPrescriptionSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.name", read_only=True)
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    image_url = serializers.SerializerMethodField()
    order_count = serializers.SerializerMethodField()

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
            "created_at",
        )
        read_only_fields = ("id", "user", "created_at")

    def get_image_url(self, obj) -> str | None:
        if not obj.image:
            return None
        request = self.context.get("request")
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url

    def get_order_count(self, obj) -> int:
        return obj.orders.count()


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
    queryset = Prescription.objects.select_related("user").prefetch_related("orders")
    filterset_fields = ["status", "user"]
    search_fields = ["user__name", "user__phone", "notes"]
    ordering_fields = ["created_at", "status"]
    # Documents are uploaded by the patient and are evidence; an admin
    # reviews them, never edits or destroys them.
    http_method_names = ["get", "patch", "head", "options"]


class AdminDoctorProfileSerializer(serializers.ModelSerializer):
    """Admin-editable clinical profile for a doctor.

    Identity (name, phone, email) belongs to the linked ``Provider`` and is
    edited through the partners endpoint, so it is read-only here — one field,
    one owner.
    """

    provider_id = serializers.IntegerField(source="provider.id", read_only=True)
    name = serializers.CharField(source="provider.name", read_only=True)
    phone = serializers.CharField(source="provider.user.phone", read_only=True)

    class Meta:
        model = DoctorProfile
        fields = (
            "id",
            "provider_id",
            "name",
            "phone",
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
            "rating_avg",
            "review_count",
            "created_at",
        )
        read_only_fields = ("id", "rating_avg", "review_count", "created_at")


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
    queryset = DiagnosticBooking.objects.prefetch_related("tests").select_related(
        "patient", "center"
    )
    filterset_fields = ["status", "patient"]
    # `tests__name` spans a many-to-many, so a booking with three matching
    # tests would be returned three times; DRF's SearchFilter detects that
    # and applies .distinct() for us.
    search_fields = ["patient__name", "patient__phone", "tests__name"]
    ordering_fields = ["created_at", "scheduled_date", "total_amount"]
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        old_status = instance.status
        response = super().partial_update(request, *args, **kwargs)
        instance.refresh_from_db()
        if instance.status != old_status:
            _notify_diagnostic_booking_status_change(instance, instance.status)
        return response
