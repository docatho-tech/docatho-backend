from django.db.models import Count
from django.db.models import DecimalField
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.http import FileResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from rest_framework import permissions
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.generics import ListAPIView
from rest_framework.generics import ListCreateAPIView
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from docatho_backend.masters.permissions import IsAdmin
from docatho_backend.masters.permissions import IsProvider
from docatho_backend.orders.analytics import REVENUE_Q
from docatho_backend.orders.models import Order
from docatho_backend.orders.paginators import GenericPaginationClass
from docatho_backend.orders.views import AdminOrderSerializer
from docatho_backend.orders.views import UpdateOrderStatusSerializer
from docatho_backend.orders.views import _notify_status_change
from docatho_backend.providers.models import Provider
from docatho_backend.providers.serializers import AdminProviderCreateSerializer
from docatho_backend.providers.serializers import AdminProviderSerializer
from docatho_backend.providers.serializers import ProviderBankSerializer
from docatho_backend.providers.serializers import ProviderSerializer
from docatho_backend.providers.serializers import UserSerializer
from docatho_backend.users.helper import generate_otp
from docatho_backend.users.models import PhoneOtp
from docatho_backend.users.models import User

DEC = DecimalField(max_digits=14, decimal_places=2)
ZERO = Value(0, output_field=DEC)

# Status transitions a fulfilling provider is allowed to make (EP-06).
PROVIDER_ALLOWED_STATUSES = {
    Order.Status.APPROVED,
    Order.Status.REJECTED,
    Order.Status.PACKED,
    Order.Status.OUT_FOR_DELIVERY,
    Order.Status.DELIVERED,
}


def _provider_for(user) -> Provider | None:
    return Provider.objects.filter(user=user).first()


class AdminProviderListCreateAPIView(ListCreateAPIView):
    """Admin: list providers (filter by ``provider_type``) and onboard new ones.

    Powers the dashboard's Pharmacies directory and the order
    "assign to pharmacy" picker (call with ``?provider_type=Chemist``).
    """

    permission_classes = [IsAdmin]
    pagination_class = GenericPaginationClass
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["provider_type"]
    search_fields = ["name", "specialty", "user__name", "user__phone"]
    queryset = Provider.objects.select_related("user").all().order_by("-created_at")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return AdminProviderCreateSerializer
        return AdminProviderSerializer

    def create(self, request, *args, **kwargs):
        serializer = AdminProviderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = serializer.save()
        return Response(
            AdminProviderSerializer(provider).data,
            status=status.HTTP_201_CREATED,
        )


class AdminProviderDetailAPIView(RetrieveUpdateDestroyAPIView):
    """Admin: retrieve/update/remove a single provider."""

    permission_classes = [IsAdmin]
    serializer_class = AdminProviderSerializer
    queryset = Provider.objects.select_related("user").all()


class SendOTPAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        phone = request.data.get("phone")
        try:
            User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        otp_value = generate_otp()
        otp_obj, _ = PhoneOtp.objects.get_or_create(
            phone_number=phone,
            defaults={"otp": otp_value},
        )
        otp_obj.refresh_code(otp_value)
        return Response({"detail": "OTP sent"}, status=status.HTTP_200_OK)


class VerifyOTPAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        phone = request.data.get("phone")
        otp = request.data.get("otp")

        try:
            otp_obj = PhoneOtp.objects.get(phone_number=phone)
        except PhoneOtp.DoesNotExist:
            return Response(
                {"detail": "OTP not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if otp_obj.otp == otp:
            return Response(
                {
                    "detail": "OTP verified",
                    "token": Token.objects.get_or_create(user=user)[0].key,
                },
                status=status.HTTP_200_OK,
            )
        return Response({"detail": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)


class ChemistOrderListAPIView(ListAPIView):
    """Incoming/assigned order queue for the current provider (EP-06)."""

    permission_classes = [IsProvider]
    pagination_class = GenericPaginationClass
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status"]
    serializer_class = AdminOrderSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()
        provider = _provider_for(self.request.user)
        return Order.objects.filter(assigned_provider=provider).order_by("-placed_at")


class ChemistOrderUpdateAPIView(APIView):
    """Provider updates the fulfilment status of one of their orders (EP-06)."""

    permission_classes = [IsProvider]

    def patch(self, request, pk):
        provider = _provider_for(request.user)
        order = Order.objects.filter(pk=pk, assigned_provider=provider).first()
        if order is None:
            return Response(
                {"detail": "Order not found or not assigned to you."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = UpdateOrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]
        if new_status not in PROVIDER_ALLOWED_STATUSES:
            return Response(
                {"detail": f"Providers cannot set status '{new_status}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            order.update_status(
                new_status=new_status,
                notes=serializer.validated_data.get("notes"),
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        _notify_status_change(order)
        return Response(
            AdminOrderSerializer(order, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class OrderDetailAPIView(APIView):
    """Provider views one of their assigned orders."""

    permission_classes = [IsProvider]

    def get(self, request, pk):
        provider = _provider_for(request.user)
        order = Order.objects.filter(pk=pk, assigned_provider=provider).first()
        if order is None:
            return Response(
                {"detail": "Order not found or not assigned to you."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            AdminOrderSerializer(order, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class OrderInvoiceAPIView(APIView):
    """Provider downloads the invoice PDF for one of their assigned orders."""

    permission_classes = [IsProvider]

    def get(self, request, pk):
        from docatho_backend.orders.invoices import get_or_create_invoice

        provider = _provider_for(request.user)
        order = Order.objects.filter(pk=pk, assigned_provider=provider).first()
        if order is None:
            return Response(
                {"detail": "Order not found or not assigned to you."},
                status=status.HTTP_404_NOT_FOUND,
            )
        invoice = get_or_create_invoice(order)
        return FileResponse(
            invoice.pdf.open("rb"),
            as_attachment=True,
            filename=f"{invoice.invoice_number}.pdf",
        )


class UserDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(
            UserSerializer(request.user).data,
            status=status.HTTP_200_OK,
        )


class ProviderProfileAPIView(APIView):
    """GET/PATCH the current provider's profile."""

    permission_classes = [IsProvider]

    def get(self, request):
        provider = _provider_for(request.user)
        return Response(ProviderSerializer(provider).data)

    def patch(self, request):
        provider = _provider_for(request.user)
        serializer = ProviderSerializer(provider, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ProviderBankAPIView(APIView):
    """GET/PATCH the current provider's bank/payout details (EP-07)."""

    permission_classes = [IsProvider]

    def get(self, request):
        provider = _provider_for(request.user)
        return Response(ProviderBankSerializer(provider).data)

    def patch(self, request):
        provider = _provider_for(request.user)
        serializer = ProviderBankSerializer(provider, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ProviderEarningsAPIView(APIView):
    """The current provider's earnings from delivered/paid orders (EP-07)."""

    permission_classes = [IsProvider]

    def get(self, request):
        provider = _provider_for(request.user)
        earned = (
            Order.objects.filter(assigned_provider=provider)
            .filter(REVENUE_Q)
            .aggregate(
                total_orders=Count("id"),
                gross=Coalesce(Sum("total"), ZERO),
                commission=Coalesce(Sum("commission_amount"), ZERO),
                payout=Coalesce(Sum("provider_earning"), ZERO),
            )
        )
        # Payout still owed for assigned orders not yet counted as revenue.
        pending = (
            Order.objects.filter(assigned_provider=provider)
            .exclude(REVENUE_Q)
            .exclude(status__in=Order.STOCK_RELEASING_STATUSES)
            .aggregate(payout=Coalesce(Sum("provider_earning"), ZERO))
        )
        return Response(
            {
                "total_orders": earned["total_orders"],
                "gross": earned["gross"],
                "commission": earned["commission"],
                "payout": earned["payout"],
                "pending_payout": pending["payout"],
            },
        )
