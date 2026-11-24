"""Storage for medical documents, which must not be world-readable.

Everything else in the product is addressed by a permanent public URL — product
photos, category art, clinic pictures — and `AWS_QUERYSTRING_AUTH = False` is
what makes those work. A prescription PDF or a lab report is the opposite case:
it names a patient and their diagnosis, so the address has to expire.

The split is per *storage*, not global: the ``"private"`` alias in
``settings.STORAGES`` points at the same bucket under its own prefix with
``default_acl="private"`` and signing switched back on. Flipping the global
setting instead would break every image URL already stored in the database.

A stored reference is ``private:<key>`` rather than a URL, because a signed URL
is only valid for minutes and must never be persisted — the serializer re-signs
the key on every read.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from django.core.files.storage import storages

#: Marks a stored string as a key in the private bucket rather than a URL.
PRIVATE_REF_PREFIX = "private:"


def private_storage():
    """The private media storage.

    A function, not a module-level instance: `FileField(storage=...)` takes a
    callable, which keeps the storage out of the migration state and resolved
    per-request instead of at import time.
    """
    return storages["private"]


def is_private_ref(value: str | None) -> bool:
    return bool(value) and value.startswith(PRIVATE_REF_PREFIX)


def private_key(value: str) -> str:
    return value[len(PRIVATE_REF_PREFIX) :]


def store_private(upload, *, prefix: str = "documents") -> str:
    """Save an uploaded file privately and return its ``private:<key>`` ref."""
    suffix = Path(upload.name or "").suffix.lower()
    name = f"{prefix}/{uuid.uuid4().hex}{suffix}"
    saved = private_storage().save(name, upload)
    return f"{PRIVATE_REF_PREFIX}{saved}"


def signed_url(value: str | None) -> str:
    """Re-sign a stored reference for one read.

    Anything that is not a private ref is returned unchanged: documents
    uploaded before this existed are plain public URLs and still have to render.
    """
    if not value:
        return ""
    if not is_private_ref(value):
        return value
    return private_storage().url(private_key(value))
