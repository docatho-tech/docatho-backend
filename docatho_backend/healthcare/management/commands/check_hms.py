"""Verify the 100ms (HMS) video integration end to end, without touching the DB.

Usage:
    uv run python manage.py check_hms                # config + credentials + template
    uv run python manage.py check_hms --create-room  # also create/disable a real room

Exits non-zero on the first failure, so it doubles as a deploy smoke check.
"""

from __future__ import annotations

import base64
import json
import time

import requests
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from docatho_backend.healthcare.hms import HMS_API_BASE
from docatho_backend.healthcare.hms import HMSClient


def _mask(value: str) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]} ({len(value)} chars)"


class Command(BaseCommand):
    help = "Check that 100ms credentials, template and roles are usable."

    def add_arguments(self, parser):
        parser.add_argument(
            "--create-room",
            action="store_true",
            help="Create a throwaway room, mint tokens for both roles, then disable it.",
        )

    def handle(self, *args, **options):
        client = HMSClient()

        self.stdout.write(self.style.MIGRATE_HEADING("Configuration"))
        self.stdout.write(f"  HMS_APP_ACCESS_KEY : {_mask(client.access_key)}")
        self.stdout.write(f"  HMS_APP_SECRET     : {_mask(client.secret)}")
        self.stdout.write(f"  HMS_TEMPLATE_ID    : {client.template_id or '(unset)'}")
        self.stdout.write(f"  patient role       : {client.patient_role}")
        self.stdout.write(f"  doctor role        : {client.doctor_role}")

        if not client.is_configured:
            raise CommandError(
                "100ms is not configured — the backend will hand out mock tokens and "
                "video will never connect. Set HMS_APP_ACCESS_KEY, HMS_APP_SECRET and "
                "HMS_TEMPLATE_ID.",
            )

        token = client.management_token()
        headers = {"Authorization": f"Bearer {token}"}

        self.stdout.write(self.style.MIGRATE_HEADING("Credentials"))
        resp = requests.get(f"{HMS_API_BASE}/templates", headers=headers, timeout=20)
        if resp.status_code == 401:
            raise CommandError("100ms rejected the management token — access key/secret mismatch.")
        resp.raise_for_status()
        templates = {t["id"]: t for t in resp.json().get("data", [])}
        self.stdout.write(self.style.SUCCESS(f"  ✓ authenticated, {len(templates)} template(s) visible"))

        self.stdout.write(self.style.MIGRATE_HEADING("Template"))
        template = templates.get(client.template_id)
        if template is None:
            raise CommandError(
                f"HMS_TEMPLATE_ID={client.template_id} is not on this account. "
                f"Available: {', '.join(templates) or 'none'}",
            )
        roles = template.get("roles") or {}
        self.stdout.write(f"  name  : {template.get('name')}")
        self.stdout.write(f"  roles : {', '.join(roles) or 'none'}")

        missing = [r for r in (client.patient_role, client.doctor_role) if r not in roles]
        if missing:
            raise CommandError(
                f"Role(s) {', '.join(missing)} missing from template '{template.get('name')}'. "
                f"Fix HMS_PATIENT_ROLE / HMS_DOCTOR_ROLE or add the roles in the 100ms dashboard.",
            )

        for label, role in (("patient", client.patient_role), ("doctor", client.doctor_role)):
            publish = ((roles[role].get("publishParams") or {}).get("allowed")) or []
            if "video" not in publish or "audio" not in publish:
                raise CommandError(
                    f"Role '{role}' ({label}) cannot publish audio+video — allowed: {publish or 'nothing'}",
                )
            self.stdout.write(self.style.SUCCESS(f"  ✓ {label} role '{role}' publishes {publish}"))

        if not options["create_room"]:
            self.stdout.write(self.style.SUCCESS("\n100ms is configured correctly."))
            self.stdout.write("Run with --create-room to exercise room creation and token minting.")
            return

        self.stdout.write(self.style.MIGRATE_HEADING("Live room"))
        room_name = f"docatho-check-{int(time.time())}"
        room = client.create_room(room_name)
        room_id = room.get("id") or room.get("room_id") or ""
        if not room_id:
            raise CommandError(f"Room creation returned no id: {json.dumps(room)[:300]}")
        self.stdout.write(self.style.SUCCESS(f"  ✓ created room {room_name} → {room_id}"))

        try:
            for label, role in (("patient", client.patient_role), ("doctor", client.doctor_role)):
                auth = client.auth_token(room_id, f"check-{label}", role)
                claims = _decode_claims(auth)
                if claims.get("room_id") != room_id or claims.get("role") != role:
                    raise CommandError(f"Minted token for {label} has wrong claims: {claims}")
                ttl = claims["exp"] - claims["iat"]
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ {label} token role={claims['role']} exp=+{ttl}s"),
                )

            for role, code in client.room_codes(room_id).items():
                where = client.prebuilt_url(code) if client.subdomain else f"code {code}"
                self.stdout.write(f"  join as {role:6s}: {where}")
            if not client.subdomain:
                self.stdout.write(
                    self.style.WARNING("  HMS_SUBDOMAIN unset — cannot build browser join URLs"),
                )
        finally:
            disabled = requests.post(
                f"{HMS_API_BASE}/rooms/{room_id}",
                headers=headers,
                json={"enabled": False},
                timeout=20,
            )
            state = "disabled" if disabled.status_code < 300 else f"NOT disabled ({disabled.status_code})"
            self.stdout.write(f"  cleanup: room {room_id} {state}")

        self.stdout.write(self.style.SUCCESS("\n100ms integration is live and working."))


def _decode_claims(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))
