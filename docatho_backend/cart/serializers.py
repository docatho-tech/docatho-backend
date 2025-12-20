from .models import Cart, CartItem
from docatho_backend.medicines.models import Medicine
from rest_framework import serializers
from decimal import Decimal

class MedicineLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medicine
        fields = ("id", "name", "image_url")


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
