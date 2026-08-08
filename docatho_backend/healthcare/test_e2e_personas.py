"""Phase 1 persona E2E tests: Patient, Provider (doctor+chemist), Admin."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import VerificationStatus
from docatho_backend.testing.factories import AddressFactory
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import DiagnosticTestFactory
from docatho_backend.testing.factories import DoctorProfileFactory
from docatho_backend.testing.factories import MedicineFactory
from docatho_backend.testing.factories import ProviderFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _add_to_cart(client, medicine, qty=1):
    resp = client.post(
        "/api/cart/add/",
        {"medicine_id": medicine.id, "quantity": qty},
        format="json",
    )
    assert resp.status_code in (200, 201), resp.data


# --------------------------------------------------------------------------- #
# Patient persona
# --------------------------------------------------------------------------- #
def test_patient_browse_doctors_and_book_appointment(auth_client):
    patient = UserFactory()
    doctor = DoctorProfileFactory()
    client = auth_client(patient)

    listing = client.get("/api/healthcare/doctors/")
    assert listing.status_code == 200
    assert any(d["id"] == doctor.id for d in listing.data.get("results", listing.data))

    detail = client.get(f"/api/healthcare/doctors/{doctor.provider.id}/")
    assert detail.status_code == 200
    assert detail.data["name"] == doctor.provider.name

    scheduled = timezone.now() + timedelta(days=2)
    booking = client.post(
        "/api/healthcare/appointments/",
        {
            "doctor": doctor.id,
            "scheduled_at": scheduled.isoformat(),
            "consultation_mode": "online",
            "symptoms": "Mild fever",
            "payment_method": "cod",
        },
        format="json",
    )
    assert booking.status_code == 201, booking.data
    appt_id = booking.data["id"]

    saved = client.post(
        "/api/healthcare/saved-doctors/",
        {"doctor_id": doctor.id},
        format="json",
    )
    assert saved.status_code == 201

    mine = client.get("/api/healthcare/appointments/")
    assert mine.status_code == 200
    assert any(a["id"] == appt_id for a in mine.data.get("results", mine.data))


def test_patient_diagnostics_wishlist_reminders(auth_client):
    patient = UserFactory()
    medicine = MedicineFactory()
    test = DiagnosticTestFactory(price=Decimal("550.00"))
    client = auth_client(patient)

    tests = client.get("/api/healthcare/diagnostic-tests/")
    assert tests.status_code == 200

    booking = client.post(
        "/api/healthcare/diagnostic-bookings/",
        {
            "test_ids": [test.id],
            "patient_address": "221B Baker Street, Mumbai",
            "scheduled_date": (timezone.localdate() + timedelta(days=3)).isoformat(),
        },
        format="json",
    )
    assert booking.status_code == 201, booking.data
    assert Decimal(str(booking.data["total_amount"])) == Decimal("550.00")

    wish = client.post(
        "/api/healthcare/wishlist/",
        {"medicine": medicine.id},
        format="json",
    )
    assert wish.status_code == 201

    reminder = client.post(
        "/api/healthcare/reminders/",
        {
            "medicine": medicine.id,
            "medicine_name": medicine.name,
            "dosage": "1 tablet",
            "reminder_times": ["09:00", "21:00"],
        },
        format="json",
    )
    assert reminder.status_code == 201

    wishlist = client.get("/api/healthcare/wishlist/")
    assert wishlist.status_code == 200
    reminders = client.get("/api/healthcare/reminders/")
    assert reminders.status_code == 200


def test_patient_ai_chat_and_prescription_analysis(auth_client):
    patient = UserFactory()
    client = auth_client(patient)

    chat = client.post(
        "/api/healthcare/ai/chat/",
        {"message": "I have a fever and headache"},
        format="json",
    )
    assert chat.status_code == 200
    assert chat.data["reply"]
    assert chat.data["session_id"]

    rx = client.post(
        "/api/healthcare/ai/prescription-analysis/",
        {"text": "Paracetamol 500mg, 1-0-1 after food"},
        format="json",
    )
    assert rx.status_code == 200
    assert rx.data["source"] in ("rule", "openai")


def test_patient_pharmacy_cod_journey(auth_client):
    patient = UserFactory()
    AddressFactory(user=patient, is_default=True)
    admin = AdminUserFactory()
    chemist = ProviderFactory()
    medicine = MedicineFactory(stock=20)
    client = auth_client(patient)

    _add_to_cart(client, medicine)
    checkout = client.post(
        "/api/orders/checkout/",
        {"payment_method": "cod"},
        format="json",
    )
    assert checkout.status_code == 201, checkout.data
    order_id = checkout.data["order"]["id"]

    auth_client(admin).patch(
        f"/api/admin/orders/{order_id}/assign-provider/",
        {"provider_id": chemist.id},
        format="json",
    )
    provider_client = auth_client(chemist.user)
    for st in ("approved", "packed", "out_for_delivery", "delivered"):
        resp = provider_client.patch(
            f"/api/providers/chemist-order-update/{order_id}/",
            {"status": st},
            format="json",
        )
        assert resp.status_code == 200

    orders = client.get("/api/orders/")
    assert orders.status_code == 200


# --------------------------------------------------------------------------- #
# Provider personas
# --------------------------------------------------------------------------- #
def test_doctor_provider_manages_appointments(auth_client):
    doctor = DoctorProfileFactory()
    patient = UserFactory()
    scheduled = timezone.now() + timedelta(days=1)
    from docatho_backend.healthcare.models import Appointment

    appt = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        scheduled_at=scheduled,
        consultation_mode="online",
        fee=doctor.fee_online,
    )
    doc_client = auth_client(doctor.provider.user)

    profile = doc_client.get("/api/healthcare/provider/doctor-profile/")
    assert profile.status_code == 200

    slots = doc_client.post(
        "/api/healthcare/provider/availability/",
        {
            "day_of_week": 1,
            "start_time": "09:00:00",
            "end_time": "12:00:00",
            "consultation_mode": "online",
        },
        format="json",
    )
    assert slots.status_code == 201

    listed = doc_client.get("/api/healthcare/provider/appointments/")
    assert listed.status_code == 200
    assert any(a["id"] == appt.id for a in listed.data)

    updated = doc_client.patch(
        "/api/healthcare/provider/appointments/",
        {"appointment_id": appt.id, "status": AppointmentStatus.CONFIRMED},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.data["status"] == AppointmentStatus.CONFIRMED


def test_chemist_provider_sees_assigned_orders_only(auth_client):
    chemist = ProviderFactory()
    other = ProviderFactory()
    patient = UserFactory()
    AddressFactory(user=patient, is_default=True)
    medicine = MedicineFactory()
    client = auth_client(patient)
    _add_to_cart(client, medicine)
    checkout = client.post("/api/orders/checkout/", {"payment_method": "cod"}, format="json")
    order_id = checkout.data["order"]["id"]
    auth_client(AdminUserFactory()).patch(
        f"/api/admin/orders/{order_id}/assign-provider/",
        {"provider_id": chemist.id},
        format="json",
    )

    mine = auth_client(chemist.user).get("/api/providers/chemist-order-list/")
    assert mine.status_code == 200
    assert any(o["id"] == order_id for o in mine.data.get("results", mine.data))

    other_list = auth_client(other.user).get("/api/providers/chemist-order-list/")
    assert other_list.status_code == 200
    assert not any(o["id"] == order_id for o in other_list.data.get("results", other_list.data))


# --------------------------------------------------------------------------- #
# Admin persona
# --------------------------------------------------------------------------- #
def test_admin_dashboard_stats_and_doctor_verification(auth_client):
    admin = AdminUserFactory()
    patient = UserFactory()
    pending_doctor = DoctorProfileFactory(
        is_verified=False,
        verification_status=VerificationStatus.PENDING,
    )
    client = auth_client(admin)

    stats = client.get("/api/healthcare/admin/dashboard-stats/")
    assert stats.status_code == 200
    assert "patients_count" in stats.data
    assert "doctors_count" in stats.data
    assert stats.data["patients_count"] >= 1

    patients = client.get("/api/healthcare/admin/patients/")
    assert patients.status_code == 200
    assert any(p["id"] == patient.id for p in patients.data.get("results", patients.data))

    doctors = client.get("/api/healthcare/admin/doctors/")
    assert doctors.status_code == 200

    verify = client.patch(
        f"/api/healthcare/admin/doctors/{pending_doctor.id}/verify/",
        {"action": "approve"},
        format="json",
    )
    assert verify.status_code == 200
    assert verify.data["is_verified"] is True

    bookings = client.get("/api/healthcare/admin/diagnostic-bookings/")
    assert bookings.status_code == 200


def test_non_admin_cannot_access_admin_healthcare(auth_client):
    user = UserFactory()
    resp = auth_client(user).get("/api/healthcare/admin/dashboard-stats/")
    assert resp.status_code == 403


def test_provider_cannot_access_patient_wishlist(auth_client):
    doctor = DoctorProfileFactory()
    resp = auth_client(doctor.provider.user).post(
        "/api/healthcare/wishlist/",
        {"medicine": MedicineFactory().id},
        format="json",
    )
    assert resp.status_code == 403
