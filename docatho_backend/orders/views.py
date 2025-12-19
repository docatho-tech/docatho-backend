from django.shortcuts import render
from uuid import uuid4
from decimal import Decimal

from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from .models import Order, OrderItem, Transaction
from .razorpay import RazorpayClient
from docatho_backend.cart.models import Cart, CartItem


class OrderItemSerializer(serializers.ModelSerializer):
    medicine_id = serializers.IntegerField(read_only=True)
    medicine_name = serializers.CharField(source="medicine.name", read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "medicine_id",
            "medicine_name",
            "quantity",
            "unit_price",
            "mrp",
            "line_total",
        )


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "order_number",
            "user",
            "address",
            "status",
            "payment_status",
            "subtotal",
            "total_mrp",
            "delivery_fee",
            "discount_amount",
            "total",
            "placed_at",
            "estimated_delivery_start",
            "estimated_delivery_end",
            "items",
        )


class CheckoutSerializer(serializers.Serializer):
    address_id = serializers.IntegerField(required=False)
    delivery_fee = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=Decimal("0.00")
    )
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=Decimal("0.00")
    )
    # optional: client can pass metadata
    notes = serializers.CharField(required=False, allow_blank=True)


class RazorpayConfirmSerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField()
    razorpay_payment_id = serializers.CharField()
    razorpay_signature = serializers.CharField()


class OrderViewSet(viewsets.ViewSet):
    permission_classes = (IsAuthenticated,)

    def list(self, request):
        qs = Order.objects.filter(user=request.user).order_by("-placed_at")
        page = self.request.query_params.get("page")
        serializer = OrderSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        order = get_object_or_404(Order, pk=pk, user=request.user)
        serializer = OrderSerializer(order, context={"request": request})
        return Response(serializer.data)

    @action(detail=False, methods=["post"])
    def checkout(self, request):
        """
        Create an Order from the user's open Cart and create a Razorpay order.
        Returns Razorpay order payload to use on client for checkout.
        """
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        cart = Cart.objects.filter(user=request.user).first()
        if not cart or not cart.items.exists():
            return Response(
                {"detail": "Cart is empty"}, status=status.HTTP_400_BAD_REQUEST
            )

        with db_transaction.atomic():
            # create order
            order_number = f"ORD{uuid4().hex[:12].upper()}"
            order = Order.objects.create(
                order_number=order_number,
                user=request.user,
                address_id=data.get("address_id") or cart.address_id,
                delivery_fee=data.get("delivery_fee", Decimal("0.00")),
                discount_amount=data.get("discount_amount", Decimal("0.00")),
                notes=data.get("notes", "") or "",
            )
            # move cart items into order items (snapshot prices)
            for it in cart.items.select_related("medicine").all():
                OrderItem.objects.create(
                    order=order,
                    medicine=it.medicine,
                    quantity=it.quantity,
                    unit_price=it.unit_price,
                    mrp=it.mrp,
                    prescription_required=False,
                )

            # recalc totals and persist
            order.recalc_totals()

            # create razorpay order
            client = RazorpayClient()
            try:
                rp_order = client.create_order(order)
            except Exception as exc:
                # revert cart status on failure
                cart.save(update_fields=["updated_at"])
                return Response(
                    {"detail": "failed to create razorpay order", "error": str(exc)},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        # return order + razorpay payload (client will use rp_order['id'] etc)
        out = {
            "order": OrderSerializer(order, context={"request": request}).data,
            "razorpay_order": rp_order,
        }
        return Response(out, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="confirm-payment")
    def confirm_payment(self, request):
        """
        Confirm payment after client-side checkout.
        Body: { razorpay_order_id, razorpay_payment_id, razorpay_signature }
        """
        serializer = RazorpayConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        client = RazorpayClient()
        try:
            tr = client.confirm_payment(
                razorpay_order_id=d["razorpay_order_id"],
                razorpay_payment_id=d["razorpay_payment_id"],
                razorpay_signature=d["razorpay_signature"],
                raw_response=None,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {"detail": "confirmation failed", "error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # return transaction + order state
        return Response(
            {
                "transaction": {
                    "id": tr.id,
                    "order_id": tr.order_id,
                    "amount": str(tr.amount),
                    "succeeded": tr.succeeded,
                    "paid_at": tr.paid_at,
                },
                "order": OrderSerializer(tr.order, context={"request": request}).data,
            }
        )


@api_view(["POST"])
@permission_classes([AllowAny])
def razorpay_webhook(request):
    """
    Public webhook endpoint for Razorpay.
    Set webhook secret in settings.RAZORPAY_WEBHOOK_SECRET.
    """
    signature = request.META.get("HTTP_X_RAZORPAY_SIGNATURE") or request.META.get(
        "HTTP_X_RAZORPAY_SIGNATURE".lower()
    )
    body = request.body or b""
    client = RazorpayClient()
    try:
        payload = client.handle_webhook(body, signature)
    except ValueError:
        return Response(
            {"detail": "invalid signature"}, status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as exc:
        return Response(
            {"detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # always return 200 for valid handling
    return Response({"status": "ok", "event": payload.get("event")})
