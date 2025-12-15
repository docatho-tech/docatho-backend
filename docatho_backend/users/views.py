import logging
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import QuerySet
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView
from django.views.generic import RedirectView
from django.views.generic import UpdateView
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView
from docatho_backend.users.helper import generate_otp
from docatho_backend.users.models import PhoneOtp, User
from docatho_backend.users.serializers import SendOtpSerializer, VerifyOtpSerializer
from rest_framework.authtoken.models import Token

logger = logging.getLogger(__name__)


class UserDetailView(LoginRequiredMixin, DetailView):
    model = User
    slug_field = "id"
    slug_url_kwarg = "id"


user_detail_view = UserDetailView.as_view()


class UserUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = User
    fields = ["name"]
    success_message = _("Information successfully updated")

    def get_success_url(self) -> str:
        assert self.request.user.is_authenticated  # type guard
        return self.request.user.get_absolute_url()

    def get_object(self, queryset: QuerySet | None = None) -> User:
        assert self.request.user.is_authenticated  # type guard
        return self.request.user


user_update_view = UserUpdateView.as_view()


class UserRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self) -> str:
        return reverse("users:detail", kwargs={"pk": self.request.user.pk})


user_redirect_view = UserRedirectView.as_view()


def _find_user_for_phone(phone_number: str):
    return User.objects.filter(phone=phone_number).first()


class SendOTPApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = SendOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone_number = serializer.validated_data["phone"]
        otp_value = generate_otp()

        user = _find_user_for_phone(phone_number)
        if not user:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not user.is_active:
            return Response(
                {"detail": "User is inactive"},
                status=status.HTTP_403_FORBIDDEN,
            )

        otp_obj, _ = PhoneOtp.objects.get_or_create(
            phone_number=phone_number,
            defaults={"otp": otp_value},
        )
        otp_obj.refresh_code(otp_value)

        logger.info("OTP sent for %s", phone_number)

        return Response({"detail": "OTP Sent Successfully"}, status=status.HTTP_200_OK)


class VerifyOtpAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = VerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone_number = serializer.validated_data["phone"]
        otp_value = serializer.validated_data["otp"]

        user = _find_user_for_phone(phone_number)
        if not user:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not user.is_active:
            return Response(
                {"detail": "User is inactive"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            otp_obj = PhoneOtp.objects.get(phone_number=phone_number)
        except PhoneOtp.DoesNotExist:
            return Response(
                {"detail": "OTP not found"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info("OTP verify attempt for %s", phone_number)

        if otp_value != (otp_obj.otp or "").strip():
            return Response(
                {"detail": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST
            )

        otp_obj.delete()

        if not user.phone:
            user.phone = phone_number
            user.save(update_fields=["phone"])

        token, _ = Token.objects.get_or_create(user=user)

        return Response({"Token": token.key}, status=status.HTTP_200_OK)


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):

        try:
            User.objects.get(phone=request.data.get("phone"))
            return Response(
                {"detail": "User with this phone number already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except User.DoesNotExist:
            user = User.objects.create_user(
                phone=request.data.get("phone"),
                name=request.data.get("name", ""),
                email=request.data.get("email", ""),
                dob=request.data.get("dob", None),
            )
            user.set_unusable_password()
            user.save()

            return Response(
                {"detail": "User registered successfully"},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.error(f"Error during user registration: {e}")
            return Response(
                {"detail": "An error occurred during registration"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
