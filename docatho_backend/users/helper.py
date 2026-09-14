from django.conf import settings
from django.db.models import Q


def generate_otp() -> str:
    # if settings.DEBUG:
    return "1234"
    # return f"{secrets.randbelow(10000):04d}"


#: Numbers are Indian unless they already say otherwise.
DEFAULT_DIALLING_CODE = "+91"


def phone_variants(phone: str) -> list[str]:
    """Every spelling of one number that might be sitting in the database.

    `User.phone` is a `PhoneNumberField`, which stores whatever it was handed.
    The apps send E.164 ("+919876543210"); the admin portal's partner forms
    were saving whatever an operator typed, which is usually the bare ten
    digits. Both are the same person, and a partner onboarded the second way
    could never sign in — `User.objects.get(phone="+91…")` simply missed them,
    and the app said "User not found" about an account the admin was looking at.

    Returns the given form first, so an exact match still wins.
    """
    raw = (phone or "").strip().replace(" ", "").replace("-", "")
    if not raw:
        return []

    code = getattr(settings, "DEFAULT_DIALLING_CODE", DEFAULT_DIALLING_CODE)
    national = raw
    for prefix in (code, code.lstrip("+"), "0"):
        if national.startswith(prefix):
            national = national[len(prefix) :]
            break

    variants = [raw, f"{code}{national}", national]
    # Order matters and duplicates do not: dict.fromkeys keeps first-seen order.
    return list(dict.fromkeys(v for v in variants if v))


def find_user_by_phone(phone: str):
    """The single user this number belongs to, however it happens to be stored.

    Returns None when nobody matches. Ambiguous data — the same number stored
    twice in different spellings — resolves to the exact match if there is one,
    otherwise the earliest account, so a sign-in never depends on row order.
    """
    from docatho_backend.users.models import User

    variants = phone_variants(phone)
    if not variants:
        return None

    matches = list(User.objects.filter(Q(phone__in=variants)).order_by("id"))
    if not matches:
        return None
    for match in matches:
        if str(match.phone) == variants[0]:
            return match
    return matches[0]


def normalise_phone(phone: str) -> str:
    """The form a number should be *written* in: E.164 with the dialling code.

    Used on the admin write paths so new partners stop landing in the database
    in a shape the apps cannot look up.
    """
    variants = phone_variants(phone)
    if not variants:
        return ""
    code = getattr(settings, "DEFAULT_DIALLING_CODE", DEFAULT_DIALLING_CODE)
    for variant in variants:
        if variant.startswith(code):
            return variant
    return variants[0]
