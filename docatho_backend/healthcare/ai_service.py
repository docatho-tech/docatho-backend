"""Rule-based health assistant with optional OpenAI fallback (EP-13).

For ~5k users the default path is fully local — keyword rules and curated
responses — so there is no external dependency or latency. When
``OPENAI_API_KEY`` is set, chat and prescription analysis can delegate to
OpenAI for richer answers.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "This is general health information, not a medical diagnosis. "
    "Please consult a qualified doctor for personal advice."
)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT_SECONDS = 20

# Hard scope for the model. Everything the assistant is allowed to be is in
# here — it is the first guardrail, and the output filters below assume it.
SYSTEM_PROMPT = (
    "You are Doc AI, the assistant inside the Docatho healthcare app in India.\n"
    "SCOPE: general health information, explaining symptoms in plain language, "
    "which kind of specialist to see, and how to use Docatho (booking doctors, "
    "lab tests, ordering medicines). Refuse anything outside that in one short "
    "sentence and offer to help with a health question instead.\n"
    "NEVER do any of the following:\n"
    "- give a diagnosis, or say what a patient 'has'\n"
    "- name, recommend, compare or substitute a specific drug or brand\n"
    "- give any dose, strength, frequency or duration of any medicine\n"
    "- tell anyone to start, stop or change a prescribed medicine\n"
    "- interpret a lab report, scan or prescription as a conclusion\n"
    "- discuss pregnancy termination, controlled substances, or self-treatment "
    "of a serious condition\n"
    "- ask for or repeat a patient's name, phone number, address, ID numbers, "
    "or any other identifying detail\n"
    "ALWAYS: keep the reply under 120 words, plain language, no markdown "
    "headings, and point to a qualified doctor for anything personal. If the "
    "message suggests an emergency, say to seek emergency care immediately and "
    "stop there."
)

# Red flags are answered without calling the model at all: a generated answer
# is a worse thing to put in front of someone describing chest pain than a
# fixed one, and it cannot be slow or fail.
EMERGENCY_PATTERNS = re.compile(
    r"\b(chest pain|heart attack|stroke|can'?t breathe|cannot breathe|"
    r"difficulty breathing|unconscious|unresponsive|seizure|fainted|"
    r"severe bleeding|bleeding heavily|coughing blood|vomiting blood|"
    r"suicide|suicidal|kill myself|end my life|self[- ]harm|overdose|"
    r"poisoned|anaphyla)",
    re.IGNORECASE,
)

EMERGENCY_REPLY = (
    "This may be a medical emergency. Please call 108 (ambulance) or go to the "
    "nearest emergency department right now — do not wait for an online answer. "
    "If you are having thoughts of harming yourself, call the Tele-MANAS "
    "helpline on 14416; someone is available at any hour."
)

# Last line of defence on the way out: anything that reads like a dose, or a
# flat diagnosis, is replaced rather than softened. Matching is deliberately
# blunt — a false positive costs one canned reply, a false negative is dosing
# advice from a chatbot.
UNSAFE_OUTPUT = re.compile(
    r"(\b\d+\s?(mg|mcg|ml|iu|g)\b)"
    r"|(\b(take|swallow|inject|apply)\b[^.]{0,40}\b(tablets?|capsules?|syrups?|"
    r"injections?|doses?|drops?)\b)"
    r"|(\b(twice|thrice|once)\s+(a|per)\s+day\b)"
    r"|(\byou (have|are suffering from|are diagnosed with)\b)",
    re.IGNORECASE,
)

UNSAFE_OUTPUT_REPLY = (
    "I can't advise on medicines or doses, and I can't tell you what condition "
    "you have — that needs a doctor who can examine you. I can help you find "
    "the right specialist on Docatho, or explain what a symptom generally means."
)

# Identifiers are stripped before anything leaves our server. The patient's own
# message is still stored intact in AIChatMessage; it is only the copy handed to
# Google that is redacted.
_PHI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
    (re.compile(r"\b(?:\+91[\-\s]?)?[6-9]\d{9}\b"), "[phone]"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[id]"),  # Aadhaar-shaped
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[id]"),  # PAN-shaped
    (re.compile(r"\b\d{12,}\b"), "[id]"),
]

MAX_MESSAGE_CHARS = 1000

# A voice note or a document the patient attaches goes to the model inline. The
# list is deliberately short: these are the types Gemini reads, and anything
# else is refused at the view rather than stored and silently ignored.
ALLOWED_AUDIO_MIME = {
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/aac",
    "audio/wav",
    "audio/webm",
    "audio/ogg",
}
ALLOWED_DOC_MIME = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
}
ALLOWED_ATTACHMENT_MIME = ALLOWED_AUDIO_MIME | ALLOWED_DOC_MIME
# 10 MB is about 10 minutes of m4a voice or a long scanned report, and it is
# also roughly where inline base64 stops being reasonable to POST.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

# What an attachment turns into when no model can read it. Saying so is the
# point — a patient who records a symptom and gets a generic text answer would
# otherwise believe the recording was heard.
ATTACHMENT_UNREAD_REPLY = (
    "I couldn't listen to or read that attachment just now. It is saved to this "
    "chat, so you can try again in a moment — or describe the same thing in a "
    "message and I'll help straight away."
)


def redact_phi(text: str) -> str:
    """Remove direct identifiers from text that is about to leave our server."""
    cleaned = text or ""
    for pattern, placeholder in _PHI_PATTERNS:
        cleaned = pattern.sub(placeholder, cleaned)
    return cleaned[:MAX_MESSAGE_CHARS]


def is_emergency(text: str) -> bool:
    return bool(EMERGENCY_PATTERNS.search(text or ""))


def is_unsafe_output(text: str) -> bool:
    return bool(UNSAFE_OUTPUT.search(text or ""))

RULE_RESPONSES: dict[str, str] = {
    "fever": (
        "A mild fever often responds to rest, fluids, and paracetamol as directed "
        "on the label. Seek urgent care if fever is above 39°C, lasts more than "
        "3 days, or is accompanied by breathing difficulty or confusion."
    ),
    "headache": (
        "For occasional headaches, rest, hydration, and OTC pain relief may help. "
        "See a doctor if headaches are sudden, severe, or come with vision changes "
        "or neck stiffness."
    ),
    "cough": (
        "Warm fluids and honey may soothe a cough. Persistent cough beyond 2 weeks, "
        "chest pain, or shortness of breath warrant a doctor visit."
    ),
    "cold": (
        "Common cold symptoms usually improve in a week with rest and fluids. "
        "Antibiotics are not needed for typical viral colds."
    ),
    "stomach": (
        "For mild stomach upset, try bland foods and small sips of water. "
        "Seek care for severe pain, blood in stool, or persistent vomiting."
    ),
    "diabetes": (
        "Blood sugar management includes diet, activity, and prescribed medication. "
        "Never adjust insulin or oral medicines without your physician."
    ),
    "blood pressure": (
        "Lifestyle changes and prescribed medicines help control blood pressure. "
        "Monitor regularly and follow your cardiologist's plan."
    ),
    "allergy": (
        "Avoid known triggers and use antihistamines as advised. "
        "For swelling of face or breathing trouble, seek emergency care immediately."
    ),
    "skin": (
        "Keep the area clean and moisturised. See a dermatologist for rashes that "
        "spread quickly, blister, or do not improve within a few days."
    ),
    "appointment": (
        "You can book a doctor on Docatho — browse specialists, pick online or "
        "in-clinic consultation, and choose a convenient slot."
    ),
    "medicine": (
        "You can order medicines from the pharmacy section. Prescription-required "
        "items need a valid Rx uploaded at checkout."
    ),
    "diagnostic": (
        "Browse diagnostic tests under the Tests tab, select a centre, and book "
        "home collection or lab visit."
    ),
}

KEYWORD_MAP: list[tuple[str, str]] = [
    ("fever", "fever"),
    ("temperature", "fever"),
    ("headache", "headache"),
    ("migraine", "headache"),
    ("cough", "cough"),
    ("cold", "cold"),
    ("flu", "cold"),
    ("stomach", "stomach"),
    ("nausea", "stomach"),
    ("diarrhea", "stomach"),
    ("diabetes", "diabetes"),
    ("blood sugar", "diabetes"),
    ("blood pressure", "blood pressure"),
    ("hypertension", "blood pressure"),
    ("allergy", "allergy"),
    ("rash", "skin"),
    ("skin", "skin"),
    ("book doctor", "appointment"),
    ("appointment", "appointment"),
    ("medicine", "medicine"),
    ("pharmacy", "medicine"),
    ("lab test", "diagnostic"),
    ("diagnostic", "diagnostic"),
]


@dataclass
class AIResponse:
    content: str
    metadata: dict[str, Any]
    source: str  # "rule" | "openai" | "fallback"


class HealthcareAIService:
    """Facade used by healthcare API views."""

    def __init__(self) -> None:
        self.openai_key = getattr(settings, "OPENAI_API_KEY", "") or ""
        self.gemini_key = getattr(settings, "GEMINI_API_KEY", "") or ""

    def chat(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        attachment: dict | None = None,
    ) -> AIResponse:
        """Answer one turn.

        `attachment` is an optional ``{"mime": str, "data": bytes}`` — a voice
        note or a document the patient sent. It is handed to the model inline;
        only Gemini can read it, so an attachment-only turn says so plainly
        rather than falling through to a text-shaped canned answer.
        """
        text = (message or "").strip()
        if not text and not attachment:
            return AIResponse(
                content="Please describe your symptoms or health question.",
                metadata={"intent": "empty"},
                source="fallback",
            )

        # Before any model call: an emergency gets the fixed answer. Note this
        # reads the typed text only — speech inside a voice note is not
        # screened, because nothing here can transcribe it before the model
        # does. See the caveat in the API docs.
        if is_emergency(text):
            return AIResponse(
                content=EMERGENCY_REPLY,
                metadata={"intent": "emergency", "guardrail": "emergency"},
                source="guardrail",
            )

        safe_text = redact_phi(text)
        safe_history = [
            {"role": h.get("role", "user"), "content": redact_phi(h.get("content", ""))}
            for h in (history or [])
            if h.get("role") in ("user", "assistant")
        ]

        if attachment:
            # Only the Gemini path can take a file. Rather than quietly answer
            # the (often empty) caption, an unreadable attachment is told about.
            if not self.gemini_key:
                return AIResponse(
                    content=ATTACHMENT_UNREAD_REPLY,
                    metadata={"intent": "attachment", "guardrail": "no_model"},
                    source="fallback",
                )
            try:
                content = self._gemini_chat(safe_text, safe_history, attachment)
            except Exception:
                logger.exception("gemini attachment turn failed")
                return AIResponse(
                    content=ATTACHMENT_UNREAD_REPLY,
                    metadata={"intent": "attachment", "guardrail": "model_error"},
                    source="fallback",
                )
            if is_unsafe_output(content):
                logger.warning("gemini attachment reply blocked by output guardrail")
                return AIResponse(
                    content=f"{UNSAFE_OUTPUT_REPLY}\n\n{DISCLAIMER}",
                    metadata={"intent": "blocked", "guardrail": "unsafe_output"},
                    source="guardrail",
                )
            return AIResponse(
                content=content,
                metadata={"intent": "attachment"},
                source="gemini",
            )

        for provider, call in (
            ("gemini", self._gemini_chat if self.gemini_key else None),
            ("openai", self._openai_chat if self.openai_key else None),
        ):
            if call is None:
                continue
            try:
                content = call(safe_text, safe_history)
            except Exception:
                logger.exception("%s chat failed; trying next source", provider)
                continue
            # Out-bound guardrail: a model answer that reads like dosing or a
            # diagnosis is dropped, not shown.
            if is_unsafe_output(content):
                logger.warning("%s reply blocked by output guardrail", provider)
                return AIResponse(
                    content=f"{UNSAFE_OUTPUT_REPLY}\n\n{DISCLAIMER}",
                    metadata={"intent": "blocked", "guardrail": "unsafe_output"},
                    source="guardrail",
                )
            return AIResponse(
                content=content,
                metadata={"intent": provider},
                source=provider,
            )

        intent = self._match_intent(text)
        body = RULE_RESPONSES.get(intent, self._generic_response())
        return AIResponse(
            content=f"{body}\n\n{DISCLAIMER}",
            metadata={"intent": intent},
            source="rule",
        )

    def analyze_prescription(self, text: str | None, image_hint: str | None = None) -> AIResponse:
        combined = " ".join(filter(None, [text, image_hint])).strip()
        if self.openai_key and combined:
            try:
                parsed = self._openai_prescription(combined)
                return AIResponse(
                    content=json.dumps(parsed),
                    metadata={"medicines": parsed.get("medicines", [])},
                    source="openai",
                )
            except Exception:
                logger.exception("OpenAI prescription analysis failed; using rules")

        medicines = self._extract_medicines_rule(combined)
        payload = {
            "medicines": medicines,
            "notes": "Extracted via rule-based parser. Upload a clearer Rx for better results.",
            "disclaimer": DISCLAIMER,
        }
        return AIResponse(
            content=json.dumps(payload),
            metadata={"medicines": medicines},
            source="rule",
        )

    def _match_intent(self, text: str) -> str:
        lower = text.lower()
        for needle, intent in KEYWORD_MAP:
            if needle in lower:
                return intent
        return "general"

    def _generic_response(self) -> str:
        return (
            "I can help with common symptoms, medicine questions, booking doctors, "
            "and lab tests on Docatho. Tell me more about what you're experiencing."
        )

    def _extract_medicines_rule(self, text: str) -> list[dict[str, str]]:
        if not text:
            return []
        candidates = re.findall(
            r"\b([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]+)?)\s+(\d+\s*mg|\d+mg)?",
            text,
        )
        seen: set[str] = set()
        results: list[dict[str, str]] = []
        for name, dose in candidates:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({"name": name.strip(), "dosage": (dose or "").strip()})
        if not results and text:
            for chunk in re.split(r"[,;\n]+", text):
                chunk = chunk.strip()
                if len(chunk) > 2:
                    results.append({"name": chunk, "dosage": ""})
        return results[:20]

    def _gemini_chat(
        self,
        message: str,
        history: list[dict[str, str]],
        attachment: dict | None = None,
    ) -> str:
        """Call Gemini over REST. No SDK — one POST, one JSON answer."""
        import base64

        import requests

        contents = [
            {
                "role": "model" if item["role"] == "assistant" else "user",
                "parts": [{"text": item.get("content", "")}],
            }
            for item in history[-6:]
        ]

        parts: list[dict] = []
        if attachment:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": attachment["mime"],
                        "data": base64.b64encode(attachment["data"]).decode(),
                    },
                },
            )
            # Without this the model tends to read a voice note back verbatim
            # or summarise a report as a conclusion, which the output guardrail
            # would then have to throw away.
            parts.append(
                {
                    "text": message
                    or (
                        "The patient sent this. If it is a voice note, answer what "
                        "they asked in it. If it is a document or photo, say in "
                        "plain language what kind of document it is and what it "
                        "generally covers — do not interpret results, give a "
                        "diagnosis, or mention any medicine or dose."
                    ),
                },
            )
        else:
            parts.append({"text": message})
        contents.append({"role": "user", "parts": parts})

        response = requests.post(
            GEMINI_URL.format(model=getattr(settings, "GEMINI_MODEL", "gemini-3.8-flash")),
            headers={"x-goog-api-key": self.gemini_key},
            json={
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": contents,
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
                # Google's own filters sit under ours; both have to pass.
                "safetySettings": [
                    {"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
                    for c in (
                        "HARM_CATEGORY_HARASSMENT",
                        "HARM_CATEGORY_HATE_SPEECH",
                        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "HARM_CATEGORY_DANGEROUS_CONTENT",
                    )
                ],
            },
            timeout=GEMINI_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            # Blocked by Google's filter, or an empty answer — either way there
            # is nothing safe to show, so fall through to the next source.
            raise ValueError("Gemini returned no candidate")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise ValueError("Gemini returned an empty answer")
        return f"{text}\n\n{DISCLAIMER}"

    def _openai_chat(self, message: str, history: list[dict[str, str]]) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_key)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        for item in history[-6:]:
            role = item.get("role", "user")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": item.get("content", "")})
        messages.append({"role": "user", "content": message})
        response = client.chat.completions.create(
            model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
            max_tokens=400,
        )
        return (response.choices[0].message.content or "").strip() + f"\n\n{DISCLAIMER}"

    def _openai_prescription(self, text: str) -> dict[str, Any]:
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_key)
        response = client.chat.completions.create(
            model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract medicine names and dosages from prescription text. "
                        "Return JSON: {\"medicines\": [{\"name\": \"...\", \"dosage\": \"...\"}], "
                        "\"notes\": \"...\"}"
                    ),
                },
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            max_tokens=500,
        )
        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)
