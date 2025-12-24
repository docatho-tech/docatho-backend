from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework import status
from docatho_backend.users.models import User
from docatho_backend.users.helper import generate_otp
from docatho_backend.users.models import PhoneOtp
from rest_framework.authtoken.models import Token
from docatho_backend.orders.models import Order
from docatho_backend.orders.views import OrderSerializer
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from docatho_backend.orders.paginators import GenericPaginationClass


class SendOTPAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        phone = request.data.get("phone")
        try:
            user = User.objects.get(phone=phone)
            if not user:
                return Response(
                    {"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND
                )
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND
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
                {"detail": "OTP not found"}, status=status.HTTP_404_NOT_FOUND
            )

        try:
            user = User.objects.get(phone=phone)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if otp_obj.otp == otp:
            return Response(
                {
                    "detail": "OTP verified",
                    "token": Token.objects.get_or_create(user=user)[0].key,
                },
                status=status.HTTP_200_OK,
            )
        else:
            return Response(
                {"detail": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST
            )


class ChemistOrderListAPIView(ListAPIView):
    """
    List orders for chemists with status filtering and pagination.
    GET /api/providers/chemist-orders/?status=placed
    """

    permission_classes = [permissions.IsAuthenticated]
    pagination_class = GenericPaginationClass
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status"]
    serializer_class = OrderSerializer
    queryset = Order.objects.all().order_by("-placed_at")


class ChemistOrderUpdateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = OrderSerializer
    queryset = Order.objects.all().order_by("-placed_at")

    def get_object(self, pk):
        return Order.objects.get(pk=pk)

    def patch(self, request, pk):
        order = self.get_object(pk=pk)
        serializer = OrderSerializer(order, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)
