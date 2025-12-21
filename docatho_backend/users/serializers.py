from rest_framework import serializers
from docatho_backend.users.models import User


class SendOtpSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32)

    def validate_phone_number(self, value: str) -> str:
        normalized = value.strip().replace(" ", "")
        if not normalized:
            raise serializers.ValidationError("Phone number is required")
        if normalized.startswith("+"):
            digits = normalized[1:]
        else:
            digits = normalized
            normalized = f"+{normalized}"
        if not digits.isdigit():
            raise serializers.ValidationError("Phone number must be numeric")
        if len(digits) < 10 or len(digits) > 15:
            raise serializers.ValidationError("Phone number must be 10-15 digits")
        return normalized


class VerifyOtpSerializer(SendOtpSerializer):
    otp = serializers.CharField(min_length=4, max_length=8)

    def validate_otp(self, value: str) -> str:
        otp = value.strip()
        if not otp.isdigit():
            raise serializers.ValidationError("OTP must contain digits only")
        return otp


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "email", "phone", "dob"]
