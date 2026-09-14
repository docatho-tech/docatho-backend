from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from docatho_backend.providers.enums import ProviderType
from docatho_backend.providers.models import OnboardingStatus
from docatho_backend.providers.models import Provider
from docatho_backend.users.helper import find_user_by_phone
from docatho_backend.users.helper import normalise_phone
from docatho_backend.users.models import User


def _provider_sale_summary(provider: Provider) -> dict:
    """Orders, gross and platform cut for the partner rail."""
    from docatho_backend.healthcare.models import Appointment
    from docatho_backend.healthcare.models import AppointmentPaymentStatus
    from docatho_backend.healthcare.models import AppointmentStatus
    from docatho_backend.healthcare.models import DiagnosticBooking
    from docatho_backend.healthcare.models import DiagnosticBookingStatus
    from docatho_backend.orders.models import Order

    rate = provider.commission_percent or Decimal("10")
    ptype = provider.provider_type

    if ptype == ProviderType.DOCTOR.value:
        qs = Appointment.objects.filter(doctor__provider=provider).exclude(
            status__in=[AppointmentStatus.CANCELLED, AppointmentStatus.REJECTED],
        )
        order_count = qs.count()
        revenue = qs.filter(
            payment_status=AppointmentPaymentStatus.PAID,
        ).aggregate(total=Sum("fee"))["total"] or Decimal("0")
    elif ptype == ProviderType.CHEMIST.value:
        qs = Order.objects.filter(assigned_provider=provider).exclude(
            status=Order.Status.CANCELLED,
        )
        order_count = qs.count()
        revenue = qs.filter(payment_status="paid").aggregate(total=Sum("total"))[
            "total"
        ] or Decimal("0")
    else:
        qs = DiagnosticBooking.objects.filter(center=provider).exclude(
            status=DiagnosticBookingStatus.CANCELLED,
        )
        order_count = qs.count()
        revenue = qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    commission = (Decimal(revenue) * Decimal(rate) / Decimal("100")).quantize(
        Decimal("0.01"),
    )
    return {
        "order_count": order_count,
        "revenue_total": str(revenue),
        "commission_total": str(commission),
    }



class UserSerializer(serializers.ModelSerializer):
    age = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "name", "email", "phone", "dob", "age", "gender", "profile_picture"]


class ProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Provider
        fields = [
            "id",
            "name",
            "specialty",
            "provider_type",
            # The partner app's home header greets by name and shows where the
            # branch is underneath it, with the logo as the avatar.
            "logo_url",
            "city",
            "location",
            "bank_account_name",
            "bank_account_number",
            "bank_ifsc",
            "upi_id",
        ]
        read_only_fields = ["id", "provider_type"]


class ProviderBankSerializer(serializers.ModelSerializer):
    """Editable bank/payout details only (EP-07)."""

    class Meta:
        model = Provider
        fields = ["bank_account_name", "bank_account_number", "bank_ifsc", "upi_id"]


class AdminProviderSerializer(serializers.ModelSerializer):
    """Read/update a provider (pharmacy/chemist/etc.) from the admin portal.

    ``phone`` and ``email`` live on the linked ``User`` and are writable here:
    they were read-only, so a PATCH carrying a corrected phone number returned
    200 with the old value still in place. Silently discarding a field an admin
    just typed is worse than rejecting it.
    """

    phone = serializers.CharField(source="user.phone", required=False)
    sale_summary = serializers.SerializerMethodField()
    complaint_count = serializers.SerializerMethodField()
    email = serializers.EmailField(
        source="user.email",
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    user_name = serializers.CharField(
        source="user.name",
        read_only=True,
        default=None,
    )
    # What the partner last signed in on, and when. Read-only: the apps write
    # them, the dashboard only reports them.
    device = serializers.CharField(source="user.device", read_only=True, default="")
    source = serializers.CharField(source="user.source", read_only=True, default="")
    last_active_at = serializers.DateTimeField(
        source="user.last_active_at",
        read_only=True,
        default=None,
    )

    def validate_phone(self, value):
        # Stored in E.164 whatever the operator typed. Saving the bare ten
        # digits locked the partner out of their own app: the apps look up
        # "+91…" and missed the row the admin was looking at.
        phone = normalise_phone(value)
        if not phone:
            raise serializers.ValidationError("Phone is required.")

        # Phone is USERNAME_FIELD but is not unique at the DB level, so nothing
        # stops two users sharing one. That breaks sign-in for both, and this
        # endpoint is the only place an admin can cause it.
        clash = User.objects.filter(phone=phone)
        if self.instance and self.instance.user_id:
            clash = clash.exclude(pk=self.instance.user_id)
        if clash.exists():
            raise serializers.ValidationError(
                "Another account already uses this phone number.",
            )
        return phone

    def update(self, instance, validated_data):
        # `source="user.x"` nests the writable user fields under "user".
        user_data = validated_data.pop("user", {})
        if user_data:
            user = instance.user
            if user is None:
                raise serializers.ValidationError(
                    {"phone": "This partner has no linked login to update."},
                )
            for field, value in user_data.items():
                setattr(user, field, value or None if field == "email" else value)
            user.save(update_fields=list(user_data.keys()))
        return super().update(instance, validated_data)

    def validate_commission_percent(self, value):
        if value is None:
            return value
        if value < 0 or value > 100:
            raise serializers.ValidationError(
                "Commission percent must be between 0 and 100.",
            )
        return value

    def get_sale_summary(self, obj):
        return _provider_sale_summary(obj)

    def get_complaint_count(self, obj):
        # Annotated by the list view; fall back to a count so the detail
        # endpoint — which fetches one row — reports the same number.
        annotated = getattr(obj, "complaint_total", None)
        if annotated is not None:
            return annotated
        return obj.complaints.count()

    class Meta:
        model = Provider
        fields = [
            "id",
            "name",
            "specialty",
            "provider_type",
            "phone",
            "email",
            "user_name",
            "device",
            "source",
            "last_active_at",
            "commission_percent",
            "sale_summary",
            "bank_account_name",
            "bank_account_number",
            "bank_ifsc",
            "upi_id",
            # Identity and location, as the partner lists and profile show it.
            "logo_url",
            "description",
            "city",
            "location",
            "address_line1",
            "address_line2",
            "pincode",
            "map_url",
            "poc_name",
            "poc_phone",
            "rating_avg",
            "review_count",
            "is_online",
            # Onboarding pipeline.
            "onboarding_status",
            "invited_at",
            "rejection_reason",
            "license_document",
            "registration_certificate",
            "gst_certificate",
            "complaint_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "sale_summary",
            "device",
            "source",
            "last_active_at",
            "invited_at",
            "rating_avg",
            "review_count",
            "complaint_count",
        ]


class AdminProviderCreateSerializer(serializers.Serializer):
    """Onboard a provider from the admin portal.

    Creates (or reuses) the backing ``User`` by phone and links a ``Provider``.
    """

    name = serializers.CharField()
    specialty = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    provider_type = serializers.ChoiceField(
        choices=ProviderType.choices(),
        default=ProviderType.CHEMIST.value,
    )
    phone = serializers.CharField()
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    # The invite wizard can send its first step alone ("Send Invitation") or
    # every step at once ("Continue" to the end), so each later-step field is
    # optional and is simply written if present.
    logo_url = serializers.CharField(required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    city = serializers.CharField(required=False, allow_blank=True, default="")
    location = serializers.CharField(required=False, allow_blank=True, default="")
    address_line1 = serializers.CharField(required=False, allow_blank=True, default="")
    address_line2 = serializers.CharField(required=False, allow_blank=True, default="")
    pincode = serializers.CharField(required=False, allow_blank=True, default="")
    map_url = serializers.CharField(required=False, allow_blank=True, default="")
    poc_name = serializers.CharField(required=False, allow_blank=True, default="")
    poc_phone = serializers.CharField(required=False, allow_blank=True, default="")
    commission_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
    )
    license_document = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    registration_certificate = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    gst_certificate = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    # Adding a partner from the directory means they are live; the invite
    # wizard sends "invited" explicitly. Defaulting the other way put every
    # partner an admin created straight into the onboarding queue and out of
    # the directory they had just added them to.
    onboarding_status = serializers.ChoiceField(
        choices=OnboardingStatus.choices,
        default=OnboardingStatus.APPROVED,
    )

    # ------------------------------------------------------------------
    # Doctor credentials.
    #
    # These live on DoctorProfile, not Provider, and are only meaningful when
    # provider_type is Doctor. They are accepted here because the row that
    # creates the doctor is the row that knows them: without these, onboarding a
    # doctor left `experience_years` at 0 and `qualifications` empty, and the only
    # way to fill them was a second call against the doctor-profile endpoint.
    # ------------------------------------------------------------------
    experience_years = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        # Nobody has practised for 80 years; a value that large is a typo or a
        # scraped "since 1945"-style string that parsed into the wrong field.
        max_value=80,
    )
    qualifications = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
    )

    #: Written straight onto the Provider when the wizard sends them.
    PROFILE_FIELDS = (
        "logo_url",
        "description",
        "city",
        "location",
        "address_line1",
        "address_line2",
        "pincode",
        "map_url",
        "poc_name",
        "poc_phone",
        "commission_percent",
        "license_document",
        "registration_certificate",
        "gst_certificate",
    )

    def _apply_profile(self, provider: Provider, validated_data: dict) -> None:
        for field in self.PROFILE_FIELDS:
            value = validated_data.get(field)
            # `None` means "not sent"; an empty string is a deliberate clear
            # only on update, and on create there is nothing to clear.
            if value in (None, ""):
                continue
            setattr(provider, field, value)

        status_value = validated_data.get("onboarding_status")
        if status_value:
            provider.onboarding_status = status_value
            if status_value == OnboardingStatus.INVITED and provider.invited_at is None:
                provider.invited_at = timezone.now()

    def create(self, validated_data):
        # Normalised on the way in, and matched against every spelling already
        # in the table — otherwise onboarding the same partner twice, once with
        # the code and once without, makes two accounts for one phone.
        phone = normalise_phone(validated_data["phone"])
        email = validated_data.get("email") or None
        user = find_user_by_phone(phone)
        if user is None:
            user = User.objects.create(
                phone=phone,
                name=validated_data["name"],
                email=email,
            )
        if hasattr(user, "provider"):
            provider = user.provider
            provider.name = validated_data["name"]
            provider.specialty = validated_data.get("specialty") or ""
            provider.provider_type = validated_data.get("provider_type")
            self._apply_profile(provider, validated_data)
            provider.save()
            self._ensure_doctor_profile(provider, validated_data)
            return provider
        provider = Provider(
            user=user,
            name=validated_data["name"],
            specialty=validated_data.get("specialty") or "",
            provider_type=validated_data.get("provider_type"),
        )
        self._apply_profile(provider, validated_data)
        provider.save()
        self._ensure_doctor_profile(provider, validated_data)
        return provider

    def _ensure_doctor_profile(self, provider: Provider, validated_data: dict) -> None:
        if provider.provider_type != ProviderType.DOCTOR.value:
            return
        from docatho_backend.healthcare.models import DoctorProfile

        profile, _ = DoctorProfile.objects.get_or_create(provider=provider)

        # `None` means "not sent" and must not clobber a value already verified by
        # an admin. Zero and [] are not sent either: the model already defaults to
        # them, so treating them as deliberate clears would let an invite wizard
        # step that omits credentials wipe the ones a later step filled in.
        updated = []
        experience = validated_data.get("experience_years")
        if experience:
            profile.experience_years = experience
            updated.append("experience_years")
        qualifications = validated_data.get("qualifications")
        if qualifications:
            profile.qualifications = qualifications
            updated.append("qualifications")
        if updated:
            profile.save(update_fields=[*updated, "updated_at"])
