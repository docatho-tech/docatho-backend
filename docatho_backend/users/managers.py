from typing import TYPE_CHECKING

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import UserManager as DjangoUserManager

if TYPE_CHECKING:
    from .models import User  # noqa: F401


class UserManager(DjangoUserManager["User"]):
    """Custom manager for the User model using phone as the USERNAME_FIELD.

    The management commands may pass additional keys such as `email` in
    extra_fields; the manager accepts them but requires `phone` to be provided
    when creating users.
    """

    def _create_user(self, phone: str, password: str | None, **extra_fields):
        """Create and save a user with the given phone and password."""
        if not phone:
            raise ValueError("The given phone number must be set")
        user = self.model(phone=phone, **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, phone: str, password: str | None = None, **extra_fields):  # type: ignore[override]
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra_fields)

    def create_superuser(self, phone: str | None = None, password: str | None = None, **extra_fields):  # type: ignore[override]
        """Create and return a superuser. Accepts phone as primary identifier.

        This signature tolerates additional kwargs (for example `email`) that the
        createsuperuser management command may pass.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        if phone is None:
            # allow phone to be provided via extra_fields if management command passed it there
            phone_val = extra_fields.pop("phone", None)
            if phone_val is None:
                raise ValueError("Superuser must have a phone number.")
            phone = phone_val

        return self._create_user(phone, password, **extra_fields)
