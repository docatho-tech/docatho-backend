import pytest
from rest_framework.test import APIClient

from docatho_backend.healthcare.models import AIChatMessage
from docatho_backend.healthcare.models import AIChatSession
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


def _session_with_turns(user, title):
    session = AIChatSession.objects.create(user=user, title=title)
    AIChatMessage.objects.create(session=session, role="user", content=title)
    AIChatMessage.objects.create(
        session=session,
        role="assistant",
        content="Since when are you getting this pain",
    )
    return session


def test_session_list_previews_the_last_reply():
    patient = UserFactory()
    _session_with_turns(patient, "Experiencing chills and fatigue")
    client = APIClient()
    client.force_authenticate(user=patient)

    res = client.get("/api/healthcare/ai/chat/sessions/")

    assert res.status_code == 200
    assert len(res.data) == 1
    assert res.data[0]["title"] == "Experiencing chills and fatigue"
    assert res.data[0]["preview"] == "Since when are you getting this pain"


def test_session_list_is_scoped_to_the_caller():
    patient = UserFactory()
    other = UserFactory()
    _session_with_turns(other, "Someone else's symptoms")
    client = APIClient()
    client.force_authenticate(user=patient)

    res = client.get("/api/healthcare/ai/chat/sessions/")

    assert res.status_code == 200
    assert res.data == []


def test_session_detail_returns_every_turn_in_order():
    patient = UserFactory()
    session = _session_with_turns(patient, "Persistent cough")
    client = APIClient()
    client.force_authenticate(user=patient)

    res = client.get(f"/api/healthcare/ai/chat/sessions/{session.pk}/")

    assert res.status_code == 200
    assert [m["role"] for m in res.data["messages"]] == ["user", "assistant"]


def test_session_detail_hides_another_users_chat():
    patient = UserFactory()
    session = _session_with_turns(UserFactory(), "Private chat")
    client = APIClient()
    client.force_authenticate(user=patient)

    res = client.get(f"/api/healthcare/ai/chat/sessions/{session.pk}/")

    assert res.status_code == 404
