import logging
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView
from django.views.generic import RedirectView
from django.views.generic import UpdateView
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView
from docatho_backend.users.helper import generate_otp
from docatho_backend.users.models import PhoneOtp, User, Address
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
            otp_obj, _ = PhoneOtp.objects.get_or_create(
                phone_number=phone_number,
                defaults={"otp": otp_value},
            )
            otp_obj.refresh_code(otp_value)
            logger.info("OTP sent for new user %s", phone_number)
            return Response(
                {"detail": "OTP Sent Successfully"}, status=status.HTTP_200_OK
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

        try:
            otp_obj = PhoneOtp.objects.get(phone_number=phone_number)
            if user and otp_obj:
                is_otp_correct = otp_value == (otp_obj.otp or "").strip()
                if not is_otp_correct:
                    return Response(
                        {"detail": "Invalid OTP"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                return Response(
                    {
                        "detail": "OTP verified",
                        "registered": True,
                        "token": Token.objects.get_or_create(user=user)[0].key,
                    },
                    status=status.HTTP_200_OK,
                )
            if not user and otp_obj:
                is_otp_correct = otp_value == (otp_obj.otp or "").strip()
                if is_otp_correct:
                    return Response(
                        {"detail": "OTP verified", "registered": False},
                        status=status.HTTP_200_OK,
                    )
                return Response({"detail": "OTP verified"}, status=status.HTTP_200_OK)
        except PhoneOtp.DoesNotExist:
            return Response(
                {"detail": "OTP not found"},
                status=status.HTTP_400_BAD_REQUEST,
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


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("name", "dob")
        extra_kwargs = {
            "name": {"required": False, "allow_blank": True},
            "dob": {"required": False, "allow_null": True},
        }


class UpdateProfileAPIView(APIView):
    """
    PATCH /api/users/update-profile/  - update current user's profile fields (except phone/email)
    """

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request):
        user = request.user
        serializer = UserUpdateSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"detail": "Profile updated successfully", "user": serializer.data},
            status=status.HTTP_200_OK,
        )


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


class AdminLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        phone = request.data.get("phone")
        password = request.data.get("password")

        try:
            user = User.objects.get(phone=phone)
            if not user.check_password(password):
                return Response(
                    {"detail": "Invalid credentials"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            if not user.is_staff:
                return Response(
                    {"detail": "User is not an admin"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            token, _ = Token.objects.get_or_create(user=user)

            return Response(
                {"token": token.key, "detail": "Admin login successful"},
                status=status.HTTP_200_OK,
            )
        except User.DoesNotExist:
            return Response(
                {"detail": "User does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        # allow common address fields; keep user read-only so creation uses request.user
        fields = (
            "id",
            "address_line1",
            "address_line2",
            "landmark",
            "city",
            "state",
            "postal_code",
            "user",
        )
        read_only_fields = ("id", "user")

    def create(self, validated_data):
        # user will be provided by view.save(user=request.user)
        return super().create(validated_data)


class CreateAddressAPIView(APIView):
    """
    POST /api/users/addresses/ - create address for request.user
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = AddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # set owner
        addr = serializer.save(user=request.user)
        # if marked default, unset other defaults for this user
        try:
            if getattr(addr, "is_default", False):
                Address.objects.filter(user=request.user).exclude(pk=addr.pk).update(
                    is_default=False
                )
        except Exception:
            pass
        out = AddressSerializer(addr, context={"request": request}).data
        return Response(out, status=status.HTTP_201_CREATED)


class UpdateAddressAPIView(APIView):
    """
    PATCH /api/users/addresses/<pk>/ - partial update address owned by request.user
    """

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        addr = get_object_or_404(Address, pk=pk, user=request.user)
        serializer = AddressSerializer(addr, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        addr = serializer.save()
        # if marked default, unset other defaults for this user
        try:
            if getattr(addr, "is_default", False):
                Address.objects.filter(user=request.user).exclude(pk=addr.pk).update(
                    is_default=False
                )
        except Exception:
            pass
        out = AddressSerializer(addr, context={"request": request}).data
        return Response(out, status=status.HTTP_200_OK)


class DashboardView(APIView):
    def get(self, request):
        marketing_urls = [
            "https://docatho-media.s3.ap-south-1.amazonaws.com/Frame+1000003879.png"
        ]
        categories = [
            {
                "id": 1,
                "name": "Medicines",
                "image_url": "https://docatho-media.s3.ap-south-1.amazonaws.com/tablet.png",
            },
            {
                "id": 2,
                "name": "Supplements",
                "image_url": "https://docatho-media.s3.ap-south-1.amazonaws.com/supplements_category.png",
            },
        ]
        return Response(
            {
                "marketing_urls": marketing_urls,
                "categories": categories,
            },
            status=status.HTTP_200_OK,
        )
