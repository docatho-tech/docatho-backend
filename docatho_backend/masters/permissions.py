"""Shared, role-based DRF permission classes for the whole project.

Roles in Docatho are not stored on a single ``role`` column. They are derived:

* **Admin**    -> ``user.is_staff`` (set on the Django user; admins log in via
  ``/api/admin-login/`` which checks ``is_staff``).
* **Provider** -> the user has a related ``providers.Provider`` row
  (OneToOne reverse accessor ``user.provider``).
* **Customer** -> any authenticated user that is neither of the above (a plain
  app user created through the OTP register flow).

These helpers centralise those checks so views don't re-implement ad-hoc
``is_staff`` / ``hasattr(user, "provider")`` logic.
"""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS
from rest_framework.permissions import BasePermission
from rest_framework.permissions import IsAdminUser as _IsAdminUser


def is_provider(user) -> bool:
    """True if ``user`` is authenticated and linked to a Provider record."""
    return bool(
        user
        and user.is_authenticated
        and _provider_exists(user),
    )


def _provider_exists(user) -> bool:
    # Cheap existence check that tolerates the reverse OneToOne not being set.
    try:
        return user.provider is not None
    except Exception:  # RelatedObjectDoesNotExist
        return False


class IsAdmin(_IsAdminUser):
    """Staff users only (admin portal)."""


class IsProvider(BasePermission):
    """Authenticated users that own a ``providers.Provider`` record."""

    message = "Only provider accounts may access this resource."

    def has_permission(self, request, view) -> bool:
        return is_provider(request.user)


class IsCustomer(BasePermission):
    """Authenticated, non-staff, non-provider users (patients)."""

    message = "Only customer accounts may access this resource."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and not user.is_staff
            and not is_provider(user),
        )


class ReadOnlyOrAdmin(BasePermission):
    """Anyone may read (SAFE_METHODS); only staff may write.

    Used for the catalogue: patients browse freely, admins manage.
    """

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsAdminOrProvider(BasePermission):
    """Admins or providers (used for fulfilment/order-management surfaces)."""

    message = "Only admin or provider accounts may access this resource."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and (user.is_staff or is_provider(user)))
