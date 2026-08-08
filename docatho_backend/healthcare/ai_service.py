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

    def chat(self, message: str, history: list[dict[str, str]] | None = None) -> AIResponse:
        text = (message or "").strip()
        if not text:
            return AIResponse(
                content="Please describe your symptoms or health question.",
                metadata={"intent": "empty"},
                source="fallback",
            )

        if self.openai_key:
            try:
                content = self._openai_chat(text, history or [])
                return AIResponse(
                    content=content,
                    metadata={"intent": "openai"},
                    source="openai",
                )
            except Exception:
                logger.exception("OpenAI chat failed; falling back to rules")

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

    def _openai_chat(self, message: str, history: list[dict[str, str]]) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_key)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are Docatho DocAI, a helpful Indian healthcare assistant. "
                    "Give concise, safe guidance and remind users to see a doctor for "
                    "serious symptoms. Never prescribe specific drug doses."
                ),
            },
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
