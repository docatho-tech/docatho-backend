from rest_framework import serializers
from docatho_backend.users.models import User, Address


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


class UserListSerializer(serializers.ModelSerializer):
    """Serializer for listing all users with basic information."""

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "dob",
            "is_active",
            "is_staff",
            "date_joined",
        ]
        read_only_fields = ["id", "date_joined"]


class AddressDetailSerializer(serializers.ModelSerializer):
    """Serializer for address details in user detail view."""

    class Meta:
        model = Address
        fields = (
            "id",
            "address_line1",
            "address_line2",
            "landmark",
            "city",
            "state",
            "postal_code",
            "country",
        )


class UserDetailSerializer(serializers.ModelSerializer):
    """Serializer for user detail view with address and orders."""

    address = serializers.SerializerMethodField()
    orders = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "dob",
            "is_active",
            "date_joined",
            "address",
            "orders",
        ]
        read_only_fields = ["id", "date_joined"]

    def get_address(self, obj):
        """Get the first address for the user."""
        address = obj.addresses.first()
        if address:
            return AddressDetailSerializer(address).data
        return None

    def get_orders(self, obj):
        """Get all orders for the user using OrderSerializer."""
        from docatho_backend.orders.views import OrderSerializer

        orders = obj.orders.all().order_by("-placed_at")
        return OrderSerializer(orders, many=True, context=self.context).data
