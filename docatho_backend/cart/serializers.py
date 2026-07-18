from decimal import Decimal

from rest_framework import serializers

from docatho_backend.medicines.models import Medicine

from .models import Cart
from .models import CartItem


class MedicineLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medicine
        fields = (
            "id",
            "name",
            "image_url",
            "schedule",
            "is_prescription_required",
            "stock",
        )


class CartItemSerializer(serializers.ModelSerializer):
    medicine = MedicineLiteSerializer(read_only=True)
    medicine_id = serializers.IntegerField(write_only=True, required=False)
    line_total = serializers.SerializerMethodField()
    is_out_of_stock = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = (
            "id",
            "medicine",
            "medicine_id",
            "quantity",
            "unit_price",
            "mrp",
            "line_total",
            "is_out_of_stock",
        )
        read_only_fields = ("unit_price", "mrp", "line_total", "is_out_of_stock")

    def get_line_total(self, obj):
        return obj.line_total or Decimal("0.00")

    def get_is_out_of_stock(self, obj):
        return bool(obj.is_out_of_stock)


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    user_name = serializers.CharField(source="user.name", read_only=True)
    address = serializers.SerializerMethodField(required=False)

    class Meta:
        model = Cart
        fields = (
            "id",
            "user",
            "user_name",
            "address",
            "total_mrp",
            "subtotal",
            "discount_amount",
            "discount_type",
            "total",
            "items",
        )
        read_only_fields = ("total_mrp", "subtotal", "total", "items")

    def get_address(self, obj):
        address = obj.user.address  # default address, else most recent
        if address:
            return {
                "id": address.id,
                "address_line1": address.address_line1,
                "address_line2": address.address_line2,
                "landmark": address.landmark,
                "city": address.city,
                "postal_code": address.postal_code,
                "state": address.state,
                "country": address.country,
                "is_default": address.is_default,
            }
        return None
