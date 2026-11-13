"""Guardrails around Doc AI: what never reaches the model, and what never
reaches the patient.

The model itself is not called here — these are the checks that must hold
whether or not Gemini answers.
"""

import pytest

from docatho_backend.healthcare.ai_service import EMERGENCY_REPLY
from docatho_backend.healthcare.ai_service import HealthcareAIService
from docatho_backend.healthcare.ai_service import is_unsafe_output
from docatho_backend.healthcare.ai_service import redact_phi


def test_emergency_message_never_reaches_the_model(settings, monkeypatch):
    settings.GEMINI_API_KEY = "test-key"
    service = HealthcareAIService()

    def explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("model was called for an emergency message")

    monkeypatch.setattr(service, "_gemini_chat", explode)

    result = service.chat("I have severe chest pain and can't breathe")
    assert result.source == "guardrail"
    assert result.content == EMERGENCY_REPLY


def test_dosing_answer_is_blocked(settings, monkeypatch):
    settings.GEMINI_API_KEY = "test-key"
    service = HealthcareAIService()
    monkeypatch.setattr(
        service,
        "_gemini_chat",
        lambda *a, **k: "Take paracetamol 500 mg twice a day for your fever.",
    )

    result = service.chat("what should I take for fever")
    assert result.source == "guardrail"
    assert "500" not in result.content
    assert "can't advise" in result.content


def test_identifiers_are_stripped_before_the_call(settings, monkeypatch):
    settings.GEMINI_API_KEY = "test-key"
    service = HealthcareAIService()
    sent = {}

    def capture(message, history):
        sent["message"] = message
        return "Rest and fluids usually help. See a doctor if it persists."

    monkeypatch.setattr(service, "_gemini_chat", capture)

    service.chat("I am Akash, 9876543210, akash@example.com, I have a cough")
    assert "9876543210" not in sent["message"]
    assert "akash@example.com" not in sent["message"]
    assert "cough" in sent["message"]


def test_model_failure_falls_back_to_rules(settings, monkeypatch):
    settings.GEMINI_API_KEY = "test-key"
    settings.OPENAI_API_KEY = ""
    service = HealthcareAIService()
    monkeypatch.setattr(
        service,
        "_gemini_chat",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("upstream down")),
    )

    result = service.chat("I have a headache")
    assert result.source == "rule"
    assert "headache" in result.content.lower() or "rest" in result.content.lower()


@pytest.mark.parametrize(
    "text",
    [
        "Take 2 tablets after food",
        "You have a bacterial infection",
        "Amoxicillin 500mg",
        "Use it twice a day",
    ],
)
def test_unsafe_outputs_detected(text):
    assert is_unsafe_output(text)


@pytest.mark.parametrize(
    "text",
    [
        "A persistent cough is worth showing to a general physician.",
        "You can book a dermatologist on Docatho under Doctors.",
    ],
)
def test_safe_outputs_pass(text):
    assert not is_unsafe_output(text)


def test_redaction_caps_length():
    assert len(redact_phi("a" * 5000)) == 1000
