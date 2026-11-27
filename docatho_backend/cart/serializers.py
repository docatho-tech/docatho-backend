from decimal import Decimal

from rest_framework import serializers

from docatho_backend.medicines.models import Medicine

from .models import Cart
from .models import CartItem
from .models import order_quote


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

    # The three figures checkout will actually use. `total` is items minus any
    # cart-level discount and excludes the delivery fee, so a screen that shows
    # it as the amount payable is lying; these come from the one function
    # checkout itself calls.
    order_discount = serializers.SerializerMethodField()
    delivery_fee = serializers.SerializerMethodField()
    payable_total = serializers.SerializerMethodField()

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
            "order_discount",
            "delivery_fee",
            "payable_total",
            "items",
        )
        read_only_fields = ("total_mrp", "subtotal", "total", "items")

    def get_order_discount(self, obj):
        return order_quote(obj.subtotal)["order_discount"]

    def get_delivery_fee(self, obj):
        return order_quote(obj.subtotal)["delivery_fee"]

    def get_payable_total(self, obj):
        return order_quote(obj.subtotal)["payable_total"]

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
