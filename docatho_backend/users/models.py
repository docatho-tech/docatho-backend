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

    def get_absolute_url(self) -> str:
        """Get URL for user's detail view.

        Returns:
            str: URL for user detail.

        """
        return reverse("users:detail", kwargs={"pk": self.id})


class PhoneOtp(BaseModel):
    """Stores OTP codes for mobile authentication flows."""

    phone_number = models.CharField(max_length=32, unique=True, db_index=True)
    otp = models.CharField(max_length=8)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Phone OTP"
        verbose_name_plural = "Phone OTPs"

    def __str__(self) -> str:  # pragma: no cover
        return f"PhoneOtp<{self.phone_number}>{self.otp}"

    def refresh_code(self, otp: str) -> None:
        self.otp = otp
        self.save(update_fields=["otp", "updated_at"])

