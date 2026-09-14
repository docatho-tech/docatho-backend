"""Signing in works whichever way the number was written down.

`User.phone` is a `PhoneNumberField`, which stores what it is given. The apps
send E.164; the admin portal's partner forms saved whatever an operator typed,
usually the bare ten digits. A partner onboarded that way could never sign in —
the app said "User not found" about an account the admin had open on screen.
"""

import pytest

from docatho_backend.testing.factories import UserFactory
from docatho_backend.users.helper import find_user_by_phone
from docatho_backend.users.helper import normalise_phone
from docatho_backend.users.helper import phone_variants

pytestmark = pytest.mark.django_db

SEND_OTP = "/api/providers/send-otp/"
VERIFY_OTP = "/api/providers/verify-otp/"


@pytest.mark.parametrize(
    ("stored", "typed"),
    [
        ("+919876543210", "+919876543210"),  # both E.164 — the happy path
        ("9876543210", "+919876543210"),     # admin saved bare digits
        ("+919876543210", "9876543210"),     # caller sent bare digits
    ],
    # Only two shapes reach the database: `PhoneNumberField` keeps E.164 and
    # bare national digits, and refuses "91…" or "09…" outright. The variant
    # helper still expands those because a *caller* can send them.
)
def test_finds_the_user_however_the_number_was_written(stored, typed):
    user = UserFactory(phone=stored)
    assert find_user_by_phone(typed) == user


def test_missing_number_finds_nobody():
    UserFactory(phone="+919876543210")
    assert find_user_by_phone("+919999999999") is None
    assert find_user_by_phone("") is None
    assert find_user_by_phone(None) is None


def test_exact_match_wins_over_another_spelling():
    """Two rows, one number. Sign-in must not depend on row order."""
    UserFactory(phone="9876543210")
    exact = UserFactory(phone="+919876543210")
    assert find_user_by_phone("+919876543210") == exact


def test_normalise_writes_the_dialling_code_back_on():
    assert normalise_phone("9876543210") == "+919876543210"
    assert normalise_phone("+919876543210") == "+919876543210"
    assert normalise_phone(" 98765 43210 ") == "+919876543210"
    assert normalise_phone("") == ""


def test_variants_keep_the_given_form_first():
    assert phone_variants("9876543210")[0] == "9876543210"


def test_partner_stored_without_a_country_code_can_sign_in(client):
    """The end-to-end case: the row the admin created, through the real views."""
    UserFactory(name="Rahul Sharma", phone="7987410870")

    sent = client.post(
        SEND_OTP,
        {"phone": "+917987410870"},
        content_type="application/json",
    )
    assert sent.status_code == 200

    verified = client.post(
        VERIFY_OTP,
        {"phone": "+917987410870", "otp": "1234"},
        content_type="application/json",
    )
    assert verified.status_code == 200
    assert verified.json()["token"]


def test_unknown_number_still_refused(client):
    resp = client.post(
        SEND_OTP,
        {"phone": "+910000000000"},
        content_type="application/json",
    )
    assert resp.status_code == 404
