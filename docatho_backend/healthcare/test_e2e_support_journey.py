"""Support ticket workflows across patient and admin."""

import pytest

from docatho_backend.healthcare.models import SupportTicketStatus
from docatho_backend.testing.factories import AdminUserFactory
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_support_ticket_patient_create_admin_lists(auth_client):
    patient = UserFactory()
    admin = AdminUserFactory()
    patient_client = auth_client(patient)
    admin_client = auth_client(admin)

    created = patient_client.post(
        "/api/healthcare/support-tickets/",
        {
            "subject": "Payment issue",
            "description": "I was charged twice for my order",
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    ticket_id = created.data["id"]

    mine = patient_client.get("/api/healthcare/support-tickets/")
    assert mine.status_code == 200
    assert any(t["id"] == ticket_id for t in mine.data.get("results", mine.data))

    admin_list = admin_client.get("/api/healthcare/support-tickets/")
    assert admin_list.status_code == 200
    assert any(t["id"] == ticket_id for t in admin_list.data.get("results", admin_list.data))

    stats = admin_client.get("/api/healthcare/admin/dashboard-stats/")
    assert stats.status_code == 200
    assert stats.data["open_support_tickets"] >= 1


def test_patient_cannot_list_other_tickets(auth_client):
    a = UserFactory()
    b = UserFactory()
    auth_client(a).post(
        "/api/healthcare/support-tickets/",
        {"subject": "Help", "description": "Need assistance"},
        format="json",
    )
    other = auth_client(b).get("/api/healthcare/support-tickets/")
    assert other.status_code == 200
    assert len(other.data.get("results", other.data)) == 0
