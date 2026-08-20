from rest_framework import serializers

from docatho_backend.providers.enums import ProviderType
from docatho_backend.providers.models import Provider
from docatho_backend.users.models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "email", "phone", "dob"]


class ProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Provider
        fields = [
            "id",
            "name",
            "specialty",
            "provider_type",
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

    def validate_phone(self, value):
        phone = (value or "").strip()
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
            "bank_account_name",
            "bank_account_number",
            "bank_ifsc",
            "upi_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


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

    def create(self, validated_data):
        phone = validated_data["phone"]
        email = validated_data.get("email") or None
        user, _ = User.objects.get_or_create(
            phone=phone,
            defaults={"name": validated_data["name"], "email": email},
        )
        if hasattr(user, "provider"):
            provider = user.provider
            provider.name = validated_data["name"]
            provider.specialty = validated_data.get("specialty") or ""
            provider.provider_type = validated_data.get("provider_type")
            provider.save()
            self._ensure_doctor_profile(provider)
            return provider
        provider = Provider.objects.create(
            user=user,
            name=validated_data["name"],
            specialty=validated_data.get("specialty") or "",
            provider_type=validated_data.get("provider_type"),
        )
        self._ensure_doctor_profile(provider)
        return provider

    def _ensure_doctor_profile(self, provider: Provider) -> None:
        if provider.provider_type != ProviderType.DOCTOR.value:
            return
        from docatho_backend.healthcare.models import DoctorProfile

        DoctorProfile.objects.get_or_create(provider=provider)
