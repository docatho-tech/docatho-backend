"""100ms (HMS) room + auth token helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any

import requests
from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import APIException

HMS_API_BASE = "https://api.100ms.live/v2"

logger = logging.getLogger(__name__)


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


class HMSNotConfiguredError(APIException, RuntimeError):
    """100ms cannot answer, and the request that needed it must say so.

    `APIException` first in the bases so DRF's own `__init__` (the one that
    sets `detail`) wins the MRO; still a `RuntimeError` for the callers that
    catch it as one. Also an `APIException` with a 503, so the two video-token endpoints turn it
    into an honest "video is not configured" rather than handing the app a
    token no room will accept. Raised per request — never at import time and
    never at startup: a missing key must break video, not the whole API.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = (
        "Video consultations are not configured on this server. "
        "Please contact support."
    )
    default_code = "hms_not_configured"


class HMSUnavailableError(APIException, RuntimeError):
    """100ms did not answer — an outage, not a misconfiguration.

    Kept apart from `HMSNotConfiguredError` because the two need different
    things done about them (wait vs. set the keys) and because a `requests`
    failure used to escape as a bare 500, which the app reads as "try again
    closer to your appointment time" — told to a patient who is already at it.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = (
        "The video service is temporarily unavailable. Please try again in a moment."
    )
    default_code = "hms_unavailable"


def mock_tokens_allowed() -> bool:
    """Whether a fake, unjoinable token may stand in for a real one."""
    return bool(getattr(settings, "HMS_ALLOW_MOCK_TOKENS", settings.DEBUG))


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

    def _post(self, path: str, **kwargs: Any) -> Any:
        """POST to 100ms, turning every transport failure into a 503.

        One chokepoint for both calls: a connection error, a timeout and a 4xx
        from 100ms are all "video cannot start right now", and none of them is
        this server's bug to report as a 500.
        """
        token = self.management_token()
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        try:
            resp = requests.post(
                f"{HMS_API_BASE}{path}", headers=headers, timeout=20, **kwargs
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("[hms] POST %s failed: %s", path, exc)
            raise HMSUnavailableError from exc

    def create_room(self, name: str) -> dict[str, Any]:
        return self._post(
            "/rooms",
            headers={"Content-Type": "application/json"},
            json={
                "name": name,
                "description": "Docatho consultation",
                "template_id": self.template_id,
            },
        )

    def room_codes(self, room_id: str) -> dict[str, str]:
        """Return {role: code} for a room, creating the codes if they don't exist.

        Room codes are what the browser-based 100ms prebuilt app joins with. The
        native apps don't use them — they join with `auth_token` — but they make
        a real call reachable from a browser, which is what the E2E suite needs.
        """
        payload = self._post(f"/room-codes/room/{room_id}")
        return {
            entry["role"]: entry["code"]
            for entry in payload.get("data", [])
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
        """A token that joins nothing. Development only — see `mock_tokens_allowed`.

        This used to be the silent fallback for blank credentials, so a
        production server with no 100ms env handed both apps a token the SDK
        rejects, and the failure surfaced as "the video call is broken".
        """
        if not mock_tokens_allowed():
            raise HMSNotConfiguredError
        return _jwt_encode(
            {"mock": True, "room_id": room_id, "user_id": user_id, "role": role, "exp": int(time.time()) + 3600},
            "docatho-dev-secret",
        )
