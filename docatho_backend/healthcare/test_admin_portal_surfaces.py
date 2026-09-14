"""
The endpoints the rebuilt admin portal added.

Each test here guards a rule that is invisible from the API shape: who may
read a consultation thread, that a settled payout always carries a date, and
that a settlement figure is earnings minus commission minus what has already
been paid — not a re-run of the same total every month.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import ConsultationMessageKind
from docatho_backend.healthcare.models import ConsultationMode
from docatho_backend.healthcare.models import DiagnosticBookingStatus
from docatho_backend.healthcare.models import SupportTicketStatus
from docatho_backend.healthcare.models import VerificationStatus
from docatho_backend.masters.buckets import APPOINTMENT_BUCKETS
from docatho_backend.masters.buckets import BOOKING_BUCKETS
from docatho_backend.masters.buckets import ORDER_BUCKETS
from docatho_backend.masters.buckets import TICKET_BUCKETS
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import Payout
from docatho_backend.orders.models import PayoutStatus
from docatho_backend.providers.enums import ProviderType
from docatho_backend.providers.models import OnboardingStatus
from docatho_backend.providers.models import Provider
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import ProviderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _appointment(patient=None, doctor=None) -> Appointment:
    return Appointment.objects.create(
        patient=patient or UserFactory(),
        doctor=doctor or DoctorProfileFactory(),
        scheduled_at=timezone.now() + timedelta(days=1),
        consultation_mode=ConsultationMode.ONLINE,
        fee=Decimal("699.00"),
    )


# ---------------------------------------------------------------- consultation


def test_consultation_thread_readable_by_patient_doctor_and_staff(auth_client):
    appointment = _appointment()
    doctor_user = appointment.doctor.provider.user
    url = f"/api/healthcare/appointments/{appointment.pk}/messages/"

    sent = auth_client(appointment.patient).post(
        url,
        {"kind": ConsultationMessageKind.TEXT, "body": "Is the report ready?"},
        format="json",
    )
    assert sent.status_code == 201, sent.data
    assert sent.data["is_from_patient"] is True

    replied = auth_client(doctor_user).post(
        url,
        {"kind": ConsultationMessageKind.TEXT, "body": "Yes, uploading now."},
        format="json",
    )
    assert replied.status_code == 201, replied.data
    assert replied.data["is_from_patient"] is False

    seen_by_admin = auth_client(AdminUserFactory()).get(url)
    assert seen_by_admin.status_code == 200
    assert [row["body"] for row in seen_by_admin.data] == [
        "Is the report ready?",
        "Yes, uploading now.",
    ]


def test_consultation_thread_hidden_from_an_unrelated_patient(auth_client):
    appointment = _appointment()
    url = f"/api/healthcare/appointments/{appointment.pk}/messages/"

    # 404, not 403: a stranger must not learn that this appointment exists.
    peeked = auth_client(UserFactory()).get(url)
    assert peeked.status_code == 404

    posted = auth_client(UserFactory()).post(
        url,
        {"body": "hello"},
        format="json",
    )
    assert posted.status_code == 404
    assert appointment.messages.count() == 0


def test_empty_text_message_is_refused(auth_client):
    appointment = _appointment()
    response = auth_client(appointment.patient).post(
        f"/api/healthcare/appointments/{appointment.pk}/messages/",
        {"kind": ConsultationMessageKind.TEXT, "body": "   "},
        format="json",
    )
    assert response.status_code == 400
    assert appointment.messages.count() == 0


# --------------------------------------------------------------------- payouts


def test_settling_a_payout_stamps_the_date_and_unsettling_clears_it(auth_client):
    admin = auth_client(AdminUserFactory())
    provider = ProviderFactory()

    created = admin.post(
        "/api/admin/payouts/",
        {"provider": provider.pk, "amount": "1200.00"},
        format="json",
    )
    assert created.status_code == 201, created.data
    assert created.data["settled_at"] is None

    settled = admin.patch(
        f"/api/admin/payouts/{created.data['id']}/",
        {"status": PayoutStatus.SETTLED},
        format="json",
    )
    assert settled.status_code == 200, settled.data
    assert settled.data["settled_at"] is not None

    reopened = admin.patch(
        f"/api/admin/payouts/{created.data['id']}/",
        {"status": PayoutStatus.PENDING},
        format="json",
    )
    assert reopened.data["settled_at"] is None


def test_a_payout_must_be_for_more_than_zero(auth_client):
    admin = auth_client(AdminUserFactory())
    response = admin.post(
        "/api/admin/payouts/",
        {"provider": ProviderFactory().pk, "amount": "0.00"},
        format="json",
    )
    assert response.status_code == 400
    assert Payout.objects.count() == 0


# ----------------------------------------------------------------- settlements


def test_settlement_subtracts_payouts_already_settled(auth_client):
    """
    Net payable is what is *still* owed.

    Without subtracting the ledger, a partner paid in full last month shows
    the same amount owed again this month, and gets paid twice.
    """
    admin = auth_client(AdminUserFactory())
    doctor = DoctorProfileFactory()
    provider = doctor.provider
    provider.commission_percent = Decimal("10")
    provider.city = "Hyderabad"
    provider.save()

    appointment = _appointment(doctor=doctor)
    appointment.payment_status = "paid"
    appointment.save(update_fields=["payment_status"])

    def row_for(pk):
        response = admin.get("/api/analytics/settlements/")
        assert response.status_code == 200, response.data
        return next(
            row for row in response.data["settlements"] if row["provider_id"] == pk
        )

    before = row_for(provider.pk)
    assert Decimal(before["revenue_total"]) == Decimal("699.00")
    assert Decimal(before["commission_total"]) == Decimal("69.90")
    assert Decimal(before["net_payable"]) == Decimal("629.10")

    Payout.objects.create(
        provider=provider,
        amount=Decimal("600.00"),
        status=PayoutStatus.SETTLED,
        settled_at=timezone.now(),
    )
    # A payout still in flight is not money the partner has; only settled
    # transfers reduce what is owed.
    Payout.objects.create(
        provider=provider,
        amount=Decimal("29.10"),
        status=PayoutStatus.PENDING,
    )

    after = row_for(provider.pk)
    assert Decimal(after["settled_total"]) == Decimal("600.00")
    assert Decimal(after["net_payable"]) == Decimal("29.10")


def test_settlements_are_admin_only(auth_client):
    assert auth_client(UserFactory()).get("/api/analytics/settlements/").status_code == 403


# ------------------------------------------------------------------ onboarding


def test_inviting_a_partner_records_the_invite_and_its_profile(auth_client):
    admin = auth_client(AdminUserFactory())
    response = admin.post(
        "/api/providers/admin/list/",
        {
            "name": "Vijaya Diagnostics",
            "provider_type": ProviderType.DIAGNOSTIC_CENTER.value,
            "phone": "+919000000123",
            "email": "vijaya@example.com",
            "city": "Hyderabad",
            "location": "Kukatpally",
            "poc_name": "Divya Reddy",
            "commission_percent": "7.50",
            "onboarding_status": OnboardingStatus.INVITED,
        },
        format="json",
    )
    assert response.status_code == 201, response.data

    provider = Provider.objects.get(pk=response.data["id"])
    assert provider.onboarding_status == OnboardingStatus.INVITED
    # The pipeline screen sorts and filters on this, so an invite with no
    # timestamp would sink to the bottom of its own queue.
    assert provider.invited_at is not None
    assert provider.city == "Hyderabad"
    assert provider.poc_name == "Divya Reddy"
    assert provider.commission_percent == Decimal("7.50")


def test_inviting_a_doctor_records_credentials_on_the_profile(auth_client):
    admin = auth_client(AdminUserFactory())
    response = admin.post(
        "/api/providers/admin/list/",
        {
            "name": "Dr. Veronica Irene Yuel",
            "specialty": "Obstetrics & Gynaecology",
            "provider_type": ProviderType.DOCTOR.value,
            "phone": "+919000000456",
            "city": "Raipur",
            "experience_years": 25,
            "qualifications": ["MBBS, MD, DNB (CMC Ludhiana)", "FICOG 2019"],
            "onboarding_status": OnboardingStatus.INVITED,
        },
        format="json",
    )
    assert response.status_code == 201, response.data

    profile = Provider.objects.get(pk=response.data["id"]).doctor_profile
    assert profile.experience_years == 25
    assert profile.qualifications == ["MBBS, MD, DNB (CMC Ludhiana)", "FICOG 2019"]
    # An invited doctor is not a listed one: the patient directory filters on
    # these two, so onboarding must never be what puts a doctor in front of
    # patients.
    assert profile.is_verified is False
    assert profile.verification_status == VerificationStatus.PENDING


def test_inviting_a_doctor_without_credentials_leaves_them_at_defaults(auth_client):
    """The wizard can send step one alone, and must not blank what it omits."""
    admin = auth_client(AdminUserFactory())
    response = admin.post(
        "/api/providers/admin/list/",
        {
            "name": "Dr. Arun Agrawalla",
            "provider_type": ProviderType.DOCTOR.value,
            "phone": "+919000000457",
            "onboarding_status": OnboardingStatus.INVITED,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    profile = Provider.objects.get(pk=response.data["id"]).doctor_profile
    assert profile.experience_years == 0
    assert profile.qualifications == []


def test_inviting_a_doctor_rejects_an_absurd_experience_figure(auth_client):
    admin = auth_client(AdminUserFactory())
    response = admin.post(
        "/api/providers/admin/list/",
        {
            "name": "Dr. Typo",
            "provider_type": ProviderType.DOCTOR.value,
            "phone": "+919000000458",
            "experience_years": 1985,
        },
        format="json",
    )
    assert response.status_code == 400, response.data
    assert "experience_years" in response.data


def test_onboarding_queue_filters_by_status(auth_client):
    admin = auth_client(AdminUserFactory())
    invited = ProviderFactory()
    invited.onboarding_status = OnboardingStatus.INVITED
    invited.invited_at = timezone.now()
    invited.save()
    ProviderFactory()  # approved by default

    response = admin.get(
        "/api/providers/admin/list/",
        {"onboarding_status": OnboardingStatus.INVITED},
    )
    assert response.status_code == 200
    ids = [row["id"] for row in response.data["results"]]
    assert ids == [invited.pk]


# --------------------------------------------------------------------- buckets


@pytest.mark.parametrize(
    ("bucket", "expected"),
    [
        ("upcoming", {"pending", "confirmed", "in_progress"}),
        ("completed", {"completed"}),
        ("cancelled", {"cancelled", "rejected"}),
        # A typo in the URL shows everything rather than an empty table that
        # reads as "this queue is clear".
        ("nonsense", {"pending", "confirmed", "in_progress", "completed",
                      "cancelled", "rejected"}),
    ],
)
def test_consultation_tabs_group_statuses(auth_client, bucket, expected):
    doctor = DoctorProfileFactory()
    for state in ("pending", "confirmed", "in_progress", "completed",
                  "cancelled", "rejected"):
        appointment = _appointment(doctor=doctor)
        appointment.status = state
        appointment.save(update_fields=["status"])

    response = auth_client(AdminUserFactory()).get(
        "/api/healthcare/appointments/",
        {"bucket": bucket, "page_size": 50},
    )
    assert response.status_code == 200
    assert {row["status"] for row in response.data["results"]} == expected


@pytest.mark.parametrize(
    ("choices", "buckets", "name"),
    [
        (AppointmentStatus, APPOINTMENT_BUCKETS, "appointments"),
        (DiagnosticBookingStatus, BOOKING_BUCKETS, "diagnostic bookings"),
        (Order.Status, ORDER_BUCKETS, "orders"),
        (SupportTicketStatus, TICKET_BUCKETS, "support tickets"),
    ],
)
def test_every_status_belongs_to_exactly_one_bucket(choices, buckets, name):
    """
    A status missing from every bucket is a row no tab can reach.

    This is the rule the dashboard's old status tabs broke — six of nine
    order statuses were listed, so orders in `approved`, `rejected` and
    `packed` were invisible. The tabs are now three buckets rather than one
    pill per status, which makes the gap easier to miss by eye and is exactly
    why it is asserted here instead.
    """
    placements: dict[str, list[str]] = {}
    for bucket, values in buckets.items():
        for value in values:
            placements.setdefault(value, []).append(bucket)

    for value in choices.values:
        assert value in placements, f"{name}: '{value}' is in no bucket"
        assert len(placements[value]) == 1, (
            f"{name}: '{value}' is in {placements[value]} — a row would appear "
            f"under two tabs and be counted twice"
        )

    # A bucket naming a status the model does not define is dead weight that
    # reads as coverage.
    unknown = set(placements) - set(choices.values)
    assert not unknown, f"{name}: buckets name statuses that do not exist: {unknown}"


def test_adding_a_partner_without_a_status_goes_live(auth_client):
    """
    The directory's "Add partner" creates a working partner, not an invite.

    Defaulting to `invited` sent every partner an admin created straight into
    the onboarding queue and out of the directory they had just been added to.
    """
    admin = auth_client(AdminUserFactory())
    response = admin.post(
        "/api/providers/admin/list/",
        {
            "name": "Walk-in Chemist",
            "provider_type": ProviderType.CHEMIST.value,
            "phone": "+919000000456",
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    provider = Provider.objects.get(pk=response.data["id"])
    assert provider.onboarding_status == OnboardingStatus.APPROVED
    assert provider.invited_at is None
