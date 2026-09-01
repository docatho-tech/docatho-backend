"""100ms (HMS) room + auth token helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import requests
from django.conf import settings

HMS_API_BASE = "https://api.100ms.live/v2"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwt_encode(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    segments = [
        _b64url(json.dumps(header, separators=(",", ":")).encode()),
        _b64url(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    signing_input = f"{segments[0]}.{segments[1]}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    segments.append(_b64url(sig))
    return ".".join(segments)


class HMSNotConfiguredError(RuntimeError):
    pass


class HMSClient:
    def __init__(
        self,
        access_key: str | None = None,
        secret: str | None = None,
        template_id: str | None = None,
    ):
        self.access_key = access_key or getattr(settings, "HMS_APP_ACCESS_KEY", "")
        self.secret = secret or getattr(settings, "HMS_APP_SECRET", "")
        self.template_id = template_id or getattr(settings, "HMS_TEMPLATE_ID", "")
        self.patient_role = getattr(settings, "HMS_PATIENT_ROLE", "guest")
        self.doctor_role = getattr(settings, "HMS_DOCTOR_ROLE", "host")
        self.subdomain = getattr(settings, "HMS_SUBDOMAIN", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.access_key and self.secret and self.template_id)

    def management_token(self) -> str:
        if not self.is_configured:
            raise HMSNotConfiguredError("100ms is not configured")
        now = int(time.time())
        payload = {
            "access_key": self.access_key,
            "type": "management",
            "version": 2,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "jti": str(uuid.uuid4()),
        }
        return _jwt_encode(payload, self.secret)

    def create_room(self, name: str) -> dict[str, Any]:
        token = self.management_token()
        resp = requests.post(
            f"{HMS_API_BASE}/rooms",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"name": name, "description": "Docatho consultation", "template_id": self.template_id},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def room_codes(self, room_id: str) -> dict[str, str]:
        """Return {role: code} for a room, creating the codes if they don't exist.

        Room codes are what the browser-based 100ms prebuilt app joins with. The
        native apps don't use them — they join with `auth_token` — but they make
        a real call reachable from a browser, which is what the E2E suite needs.
        """
        token = self.management_token()
        resp = requests.post(
            f"{HMS_API_BASE}/room-codes/room/{room_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        resp.raise_for_status()
        return {
            entry["role"]: entry["code"]
            for entry in resp.json().get("data", [])
            if entry.get("enabled", True)
        }

    def prebuilt_url(self, code: str) -> str:
        if not self.subdomain:
            raise HMSNotConfiguredError("HMS_SUBDOMAIN is not set")
        return f"https://{self.subdomain}.app.100ms.live/meeting/{code}"

    def auth_token(self, room_id: str, user_id: str, role: str) -> str:
        if not self.is_configured:
            raise HMSNotConfiguredError("100ms is not configured")
        now = int(time.time())
        payload = {
            "access_key": self.access_key,
            "room_id": room_id,
            "user_id": user_id,
            "role": role,
            "type": "app",
            "version": 2,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "jti": str(uuid.uuid4()),
        }
        return _jwt_encode(payload, self.secret)

    def dev_mock_token(self, room_id: str, user_id: str, role: str) -> str:
        return _jwt_encode(
            {"mock": True, "room_id": room_id, "user_id": user_id, "role": role, "exp": int(time.time()) + 3600},
            "docatho-dev-secret",
        )
