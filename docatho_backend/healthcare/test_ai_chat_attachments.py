"""Voice notes and documents sent into a Doc AI chat.

Two things are being protected here: what the endpoint accepts (type and size
are checked before anything is stored), and what the patient is told when the
attachment could not actually be read.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from docatho_backend.healthcare.ai_service import ATTACHMENT_UNREAD_REPLY
from docatho_backend.healthcare.ai_service import HealthcareAIService
from docatho_backend.healthcare.models import AIChatMessage
from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def patient():
    return UserFactory()


@pytest.fixture
def client(patient):
    api = APIClient()
    api.force_authenticate(user=patient)
    return api


def voice(name="note.m4a", size=64):
    return SimpleUploadedFile(name, b"0" * size, content_type="audio/m4a")


def test_a_voice_note_is_stored_against_the_turn(client, monkeypatch):
    monkeypatch.setattr(
        HealthcareAIService,
        "chat",
        lambda self, message, history=None, attachment=None: type(
            "R", (), {"content": "Thanks, I heard that.", "source": "gemini", "metadata": {}},
        )(),
    )

    response = client.post(
        "/api/healthcare/ai/chat/",
        {"attachment": voice(), "duration_ms": "5200"},
        format="multipart",
    )

    assert response.status_code == 200
    stored = AIChatMessage.objects.get(role="user")
    assert stored.attachment_kind == "audio"
    assert stored.attachment_name == "note.m4a"
    assert stored.metadata["duration_ms"] == 5200
    # No typed text is fine for a voice note; the content column stays empty.
    assert stored.content == ""


def test_an_unsupported_file_is_refused_before_it_is_stored(client):
    bad = SimpleUploadedFile("script.exe", b"MZ", content_type="application/x-msdownload")

    response = client.post(
        "/api/healthcare/ai/chat/",
        {"attachment": bad},
        format="multipart",
    )

    assert response.status_code == 400
    assert AIChatMessage.objects.count() == 0


def test_an_oversized_attachment_is_refused(client):
    response = client.post(
        "/api/healthcare/ai/chat/",
        {"attachment": voice(size=11 * 1024 * 1024)},
        format="multipart",
    )

    assert response.status_code == 400
    assert AIChatMessage.objects.count() == 0


def test_an_empty_turn_is_still_refused(client):
    response = client.post("/api/healthcare/ai/chat/", {"message": "  "}, format="multipart")

    assert response.status_code == 400


def test_an_unreadable_attachment_says_so_rather_than_answering_the_caption(settings):
    """With no Gemini key nothing can read the file, and the patient is told.

    The failure that matters is the quiet one: answering from the rule table
    would look like the recording had been listened to.
    """
    settings.GEMINI_API_KEY = ""
    settings.OPENAI_API_KEY = ""

    result = HealthcareAIService().chat(
        "",
        [],
        attachment={"mime": "audio/m4a", "data": b"0" * 16},
    )

    assert result.content == ATTACHMENT_UNREAD_REPLY


def test_the_output_guardrail_still_applies_to_an_attachment_answer(settings, monkeypatch):
    settings.GEMINI_API_KEY = "test-key"
    service = HealthcareAIService()
    monkeypatch.setattr(
        service,
        "_gemini_chat",
        lambda *a, **k: "Your report says you have anaemia — take iron 100 mg daily.",
    )

    result = service.chat(
        "",
        [],
        attachment={"mime": "application/pdf", "data": b"%PDF-"},
    )

    assert result.source == "guardrail"
    assert "100" not in result.content
