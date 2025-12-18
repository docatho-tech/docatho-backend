from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.db.models import CharField
from django.db.models import EmailField
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from phonenumber_field.modelfields import PhoneNumberField

from docatho_backend.masters.models import BaseModel
from django.db import models
from .managers import UserManager


class User(AbstractUser):
    """
    Default custom user model for docatho_backend.
    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """

    # First and last name do not cover name patterns around the globe
    name = CharField(_("Name of User"), blank=True, max_length=255)
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]
    email = EmailField(_("email address"), unique=True)
    username = None  # type: ignore[assignment]
    phone = PhoneNumberField(_("Phone Number"), blank=True)
    dob = models.DateField(_("Date of Birth"), blank=True, null=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()

    def __str__(self) -> str:
        """Return a plain string for the user suitable for display in admin and logs.

        Prefer the E.164 form of the phone number if available, otherwise fall
        back to the user's name, then id.
        """
        phone = getattr(self, "phone", None)
        if phone is not None:
            # PhoneNumberField yields a PhoneNumber object; prefer as_e164
            phone_e164 = getattr(phone, "as_e164", None)
            if phone_e164:
                return phone_e164
            return str(phone)

        if self.name:
            return self.name

        return str(getattr(self, "id", ""))

    def get_absolute_url(self) -> str:
        """Get URL for user's detail view.

        Returns:
            str: URL for user detail.

        """
        return reverse("users:detail", kwargs={"pk": self.phone})


class PhoneOtp(BaseModel):
    """Stores OTP codes for mobile authentication flows."""

    # Use PhoneNumberField so phone objects are stored in canonical form.
    phone_number = PhoneNumberField(max_length=32, unique=True, db_index=True)
    otp = models.CharField(max_length=8)

    class Meta(BaseModel.Meta):
        ordering = ["-updated_at"]
        verbose_name = "Phone OTP"
        verbose_name_plural = "Phone OTPs"

    def __str__(self) -> str:  # pragma: no cover
        # PhoneNumberField returns a PhoneNumber object; prefer .as_e164 when available
        phone_repr = getattr(self.phone_number, "as_e164", None)
        if phone_repr is None:
            phone_repr = str(self.phone_number)
        return f"PhoneOtp<{phone_repr}>{self.otp}"

    def refresh_code(self, otp: str) -> None:
        self.otp = otp
        self.save(update_fields=["otp", "updated_at"])


class Address(BaseModel):
    """Stores user addresses."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=100)

    class Meta(BaseModel.Meta):
        ordering = ["-updated_at"]
        verbose_name = "Address"
        verbose_name_plural = "Addresses"

    def __str__(self) -> str:
        return f"Address<{self.pk}> {self.address_line1}, {self.city}"
