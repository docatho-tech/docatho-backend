from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction as db_transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from rest_framework import serializers
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from docatho_backend.cart.models import Cart
from docatho_backend.masters.permissions import IsAdmin
from docatho_backend.notifications.models import NotificationType
from docatho_backend.notifications.services import notify
from docatho_backend.orders.paginators import GenericPaginationClass
from docatho_backend.users.views import AddressSerializer

from .invoices import get_or_create_invoice
from .models import Order
from .models import OrderItem
from .models import Prescription
from .models import Transaction
from .razorpay import RazorpayClient


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
class PrescriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Prescription
        fields = ("id", "image", "status", "notes", "created_at")
        read_only_fields = ("id", "status", "created_at")


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
            "prescription_required",
            "line_total",
        )


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    address = AddressSerializer(read_only=True)
    user_name = serializers.CharField(source="user.name", read_only=True)
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    provider_name = serializers.CharField(
        source="assigned_provider.name", read_only=True, default=None,
    )

    class Meta:
        model = Order
        fields = (
            "id",
            "order_number",
            "user",
            "user_name",
            "user_phone",
            "address",
            "assigned_provider",
            "provider_name",
            "prescription",
            "status",
            "payment_status",
            "payment_method",
            "subtotal",
            "total_mrp",
            "delivery_fee",
            "discount_amount",
            "total",
            "placed_at",
            "estimated_delivery_mins",
            "items",
        )


class AdminOrderSerializer(OrderSerializer):
    """Adds fulfilment/money fields visible only to staff & providers."""

    class Meta(OrderSerializer.Meta):
        fields = OrderSerializer.Meta.fields + (
            "delivered_at",
            "commission_rate",
            "commission_amount",
            "provider_earning",
            "stock_reserved",
        )


class CheckoutSerializer(serializers.Serializer):
    # delivery_fee & discount are server-controlled (see checkout logic).
    notes = serializers.CharField(required=False, allow_blank=True)
    address_id = serializers.IntegerField(required=False)
    payment_method = serializers.ChoiceField(
        choices=Order.PaymentMethod.choices,
        required=False,
        default=Order.PaymentMethod.ONLINE,
    )
    prescription_id = serializers.IntegerField(required=False)


class RazorpayConfirmSerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField()
    razorpay_payment_id = serializers.CharField()
    razorpay_signature = serializers.CharField()


class UpdateOrderStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.Status.choices)
    notes = serializers.CharField(required=False, allow_blank=True)


class AssignProviderSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField()


class TransactionSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    order_id = serializers.IntegerField(source="order.id", read_only=True)
    user_name = serializers.CharField(source="order.user.name", read_only=True)
    user_phone = serializers.CharField(source="order.user.phone", read_only=True)

    class Meta:
        model = Transaction
        fields = (
            "id",
            "order_id",
            "order_number",
            "user_name",
            "user_phone",
            "provider",
            "payment_method",
            "transaction_order_id",
            "razorpay_payment_id",
            "amount",
            "succeeded",
            "paid_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


# --------------------------------------------------------------------------- #
# Prescriptions
# --------------------------------------------------------------------------- #
class PrescriptionViewSet(viewsets.ModelViewSet):
    """Upload & list the current user's prescriptions (EP-02 Rx upload)."""

    serializer_class = PrescriptionSerializer
    permission_classes = (IsAuthenticated,)
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Prescription.objects.none()
        return Prescription.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# --------------------------------------------------------------------------- #
# Orders (patient-facing)
# --------------------------------------------------------------------------- #
class OrderViewSet(viewsets.ViewSet):
    permission_classes = (IsAuthenticated,)

    def list(self, request):
        qs = Order.objects.filter(user=request.user).order_by("-placed_at")
        serializer = OrderSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        order = get_object_or_404(Order, pk=pk, user=request.user)
        serializer = OrderSerializer(order, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["patch"], url_path="update-status")
    def update_status(self, request, pk=None):
        """Customer-initiated status changes (e.g. cancel their own order)."""
        order = get_object_or_404(Order, pk=pk, user=request.user)
        serializer = UpdateOrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order.update_status(
                new_status=serializer.validated_data["status"],
                notes=serializer.validated_data.get("notes"),
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            OrderSerializer(order, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"])
    def invoice(self, request, pk=None):
        """Download the order's invoice PDF (EP-03)."""
        order = get_object_or_404(Order, pk=pk, user=request.user)
        invoice = get_or_create_invoice(order)
        return FileResponse(
            invoice.pdf.open("rb"),
            as_attachment=True,
            filename=f"{invoice.invoice_number}.pdf",
            content_type="application/pdf",
        )

    @action(detail=False, methods=["post"])
    def checkout(self, request):
        """Create an Order from the user's Cart.

        Enforces the prescription gate, supports online (Razorpay) and COD, and
        applies server-controlled delivery fee, discount and commission split.
        """
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payment_method = data.get("payment_method", Order.PaymentMethod.ONLINE)

        cart = Cart.objects.filter(user=request.user).first()
        if not cart or not cart.items.exists():
            return Response(
                {"detail": "Cart is empty"}, status=status.HTTP_400_BAD_REQUEST,
            )

        cart_items = list(cart.items.select_related("medicine").all())

        # --- Prescription gate -------------------------------------------- #
        rx_items = [ci for ci in cart_items if ci.medicine.is_prescription_required]
        prescription = None
        if rx_items:
            prescription_id = data.get("prescription_id")
            if not prescription_id:
                return Response(
                    {
                        "detail": "A prescription is required for one or more items "
                        "in your cart.",
                        "prescription_required_for": [
                            ci.medicine.name for ci in rx_items
                        ],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            prescription = Prescription.objects.filter(
                pk=prescription_id, user=request.user,
            ).first()
            if prescription is None:
                return Response(
                    {"detail": "Invalid prescription."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # --- Stock availability (soft check before creating the order) ---- #
        shortages = [
            ci.medicine.name
            for ci in cart_items
            if ci.medicine.stock < ci.quantity
        ]
        if shortages:
            return Response(
                {"detail": "Out of stock", "items": shortages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Resolve delivery address ------------------------------------- #
        address = request.user.address
        address_id = data.get("address_id")
        if address_id:
            from docatho_backend.users.models import Address

            address = Address.objects.filter(pk=address_id, user=request.user).first()
            if address is None:
                return Response(
                    {"detail": "Invalid address."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with db_transaction.atomic():
            order = Order.objects.create(
                order_number=f"ORD{uuid4().hex[:12].upper()}",
                user=request.user,
                address=address,
                prescription=prescription,
                payment_method=payment_method,
                delivery_fee=Decimal("0.00"),
                discount_amount=Decimal("0.00"),
                notes=data.get("notes", "") or "",
            )

            for ci in cart_items:
                OrderItem.objects.create(
                    order=order,
                    medicine=ci.medicine,
                    quantity=ci.quantity,
                    unit_price=ci.unit_price,
                    mrp=ci.mrp,
                    prescription_required=ci.medicine.is_prescription_required,
                )

            order.recalc_totals()

            # Server-controlled money rules.
            discount_percent = Decimal(str(settings.PHARMACY_ORDER_DISCOUNT_PERCENT))
            discount_amount = (
                order.subtotal * (discount_percent / Decimal("100"))
            ).quantize(Decimal("0.01"))
            order.discount_amount = min(discount_amount, order.subtotal)
            order.delivery_fee = Decimal(str(settings.PHARMACY_DELIVERY_FEE)).quantize(
                Decimal("0.01"),
            )
            order.recalc_totals()
            order.compute_commission()

            if payment_method == Order.PaymentMethod.COD:
                # COD commits immediately: deduct stock and empty the cart.
                try:
                    order.reserve_stock()
                except ValueError as exc:
                    return Response(
                        {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST,
                    )
                cart.clear()
                rp_order = None
            else:
                # Online: create Razorpay order; stock is reserved on payment
                # confirmation so abandoned carts don't hold inventory.
                client = RazorpayClient()
                try:
                    rp_order = client.create_order(order)
                except Exception as exc:  # network / gateway error
                    raise serializers.ValidationError(
                        {"detail": "failed to create razorpay order", "error": str(exc)},
                    ) from exc

        _notify_order_placed(order)

        return Response(
            {
                "order": OrderSerializer(order, context={"request": request}).data,
                "razorpay_order": rp_order,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="confirm-payment")
    def confirm_payment(self, request):
        """Confirm a Razorpay payment, reserve stock and empty the cart."""
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

        if tr.succeeded:
            order = tr.order
            try:
                order.reserve_stock()
            except ValueError:
                # Payment already captured; flag the shortage for ops instead of
                # failing the confirmation.
                order.update_status(
                    Order.Status.PROCESSING,
                    notes="Stock shortage after payment; needs manual review.",
                )
            try:
                cart = Cart.objects.filter(user=order.user).first()
                if cart and cart.items.exists():
                    cart.clear()
            except Exception:
                pass
            notify(
                order.user,
                NotificationType.PAYMENT,
                "Payment received",
                f"We received your payment for order {order.order_number}.",
                order=order,
            )

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
            },
        )


def _notify_order_placed(order: Order) -> None:
    notify(
        order.user,
        NotificationType.ORDER_PLACED,
        "Order placed",
        f"Your order {order.order_number} has been placed.",
        order=order,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def razorpay_webhook(request):
    """Public webhook endpoint for Razorpay."""
    signature = request.META.get("HTTP_X_RAZORPAY_SIGNATURE")
    body = request.body or b""
    client = RazorpayClient()
    try:
        payload = client.handle_webhook(body, signature)
    except ValueError:
        return Response(
            {"detail": "invalid signature"}, status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        return Response(
            {"detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response({"status": "ok", "event": payload.get("event")})


# --------------------------------------------------------------------------- #
# Admin order management (EP-10) & money (EP-11)
# --------------------------------------------------------------------------- #
class AdminOrderList(viewsets.ReadOnlyModelViewSet):
    """Admin: list/retrieve all orders, update status, assign providers."""

    permission_classes = (IsAdmin,)
    pagination_class = GenericPaginationClass
    serializer_class = AdminOrderSerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_fields = ["status", "payment_status", "payment_method", "assigned_provider"]
    search_fields = ["order_number", "user__name", "user__phone"]
    queryset = Order.objects.all().order_by("-placed_at")

    @action(detail=True, methods=["patch"], url_path="update-status")
    def update_status(self, request, pk=None):
        order = get_object_or_404(Order, pk=pk)
        serializer = UpdateOrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order.update_status(
                new_status=serializer.validated_data["status"],
                notes=serializer.validated_data.get("notes"),
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        _notify_status_change(order)
        return Response(
            AdminOrderSerializer(order, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["patch"], url_path="assign-provider")
    def assign_provider(self, request, pk=None):
        from docatho_backend.providers.models import Provider

        order = get_object_or_404(Order, pk=pk)
        serializer = AssignProviderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = get_object_or_404(
            Provider, pk=serializer.validated_data["provider_id"],
        )
        order.assigned_provider = provider
        order.save(update_fields=["assigned_provider", "updated_at"])
        if provider.user_id:
            notify(
                provider.user,
                NotificationType.GENERIC,
                "New order assigned",
                f"Order {order.order_number} has been assigned to you.",
                order=order,
            )
        return Response(
            AdminOrderSerializer(order, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


def _notify_status_change(order: Order) -> None:
    mapping = {
        Order.Status.APPROVED: (NotificationType.ORDER_APPROVED, "Order approved"),
        Order.Status.REJECTED: (NotificationType.ORDER_REJECTED, "Order rejected"),
        Order.Status.PACKED: (NotificationType.ORDER_PACKED, "Order packed"),
        Order.Status.OUT_FOR_DELIVERY: (
            NotificationType.OUT_FOR_DELIVERY,
            "Out for delivery",
        ),
        Order.Status.DELIVERED: (NotificationType.ORDER_DELIVERED, "Order delivered"),
        Order.Status.CANCELLED: (NotificationType.ORDER_CANCELLED, "Order cancelled"),
    }
    entry = mapping.get(order.status)
    if entry:
        ntype, title = entry
        notify(
            order.user,
            ntype,
            title,
            f"Order {order.order_number}: {title.lower()}.",
            order=order,
        )


class TransactionListView(viewsets.ReadOnlyModelViewSet):
    """Transactions: own for customers, all for staff (EP-11 reports)."""

    permission_classes = (IsAuthenticated,)
    pagination_class = GenericPaginationClass
    serializer_class = TransactionSerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_fields = ["succeeded", "provider", "payment_method"]
    search_fields = [
        "razorpay_payment_id",
        "transaction_order_id",
        "order__order_number",
    ]
    queryset = Transaction.objects.all().order_by("-paid_at", "-created_at")

    def get_queryset(self):
        queryset = super().get_queryset()
        if not self.request.user.is_staff:
            queryset = queryset.filter(order__user=self.request.user)
        return queryset
