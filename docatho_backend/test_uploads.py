"""Tests for the shared image upload endpoint.

The interesting cases are all rejections: the endpoint takes a file from a
browser and decides where it lands on our storage, so what it refuses matters
more than what it accepts.
"""

import struct
import zlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from docatho_backend.users.models import User

UPLOAD_URL = "/api/uploads/"


def png_bytes() -> bytes:
    """The smallest valid PNG, built rather than committed as a binary."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def png_upload(name: str = "photo.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, png_bytes(), content_type="image/png")


@pytest.fixture
def admin_client(db) -> APIClient:
    user = User.objects.create_user(phone="+919000000001", password="pw")
    user.is_staff = True
    user.save()
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_admin_can_upload_an_image(admin_client):
    response = admin_client.post(
        UPLOAD_URL, {"file": png_upload()}, format="multipart",
    )

    assert response.status_code == 201, response.data
    assert response.data["url"].endswith(".png")


def test_the_returned_url_is_absolute(admin_client):
    """The dashboard is served from another origin.

    Local-disk storage answers "/media/...", which the browser would resolve
    against the dashboard's host rather than the API's, so the <img> 404s and
    the saved picture looks broken.
    """
    response = admin_client.post(
        UPLOAD_URL, {"file": png_upload()}, format="multipart",
    )

    assert response.status_code == 201, response.data
    assert response.data["url"].startswith("http"), response.data["url"]


def test_the_stored_name_does_not_come_from_the_client(admin_client):
    """The filename decides where the bytes land, so it is never trusted.

    A name like this one is how an upload escapes its prefix and overwrites
    something it has no business touching.
    """
    response = admin_client.post(
        UPLOAD_URL,
        {"file": png_upload("../../settings.png")},
        format="multipart",
    )

    assert response.status_code == 201, response.data
    assert ".." not in response.data["url"]
    assert "settings" not in response.data["url"]


def test_a_non_image_is_refused(admin_client):
    text = SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain")

    response = admin_client.post(UPLOAD_URL, {"file": text}, format="multipart")

    assert response.status_code == 400


def test_an_image_extension_over_a_non_image_type_is_refused(admin_client):
    """Both the extension and the declared type have to be an image.

    Checking only the suffix lets `payload.png` through with any content type
    the client cares to claim.
    """
    liar = SimpleUploadedFile("payload.png", b"<script>", content_type="text/html")

    response = admin_client.post(UPLOAD_URL, {"file": liar}, format="multipart")

    assert response.status_code == 400


def test_svg_is_refused(admin_client):
    """SVG can carry script and is served from our own domain."""
    svg = SimpleUploadedFile(
        "logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        content_type="image/svg+xml",
    )

    response = admin_client.post(UPLOAD_URL, {"file": svg}, format="multipart")

    assert response.status_code == 400


def test_an_oversized_image_is_refused(admin_client):
    big = SimpleUploadedFile(
        "big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024 + 1),
        content_type="image/png",
    )

    response = admin_client.post(UPLOAD_URL, {"file": big}, format="multipart")

    assert response.status_code == 400
    assert "5 MB" in response.data["detail"]


def test_a_pdf_is_accepted(admin_client):
    """A medical licence is rarely a photograph."""
    pdf = SimpleUploadedFile(
        "licence.pdf", b"%PDF-1.4 test", content_type="application/pdf",
    )

    response = admin_client.post(UPLOAD_URL, {"file": pdf}, format="multipart")

    assert response.status_code == 201, response.data
    assert response.data["url"].endswith(".pdf")


def test_a_request_with_no_file_is_refused(admin_client):
    response = admin_client.post(UPLOAD_URL, {}, format="multipart")

    assert response.status_code == 400


def test_a_non_admin_cannot_upload(db):
    user = User.objects.create_user(phone="+919000000002", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(UPLOAD_URL, {"file": png_upload()}, format="multipart")

    assert response.status_code == 403


def test_an_anonymous_visitor_cannot_upload(db):
    response = APIClient().post(
        UPLOAD_URL, {"file": png_upload()}, format="multipart",
    )

    assert response.status_code in (401, 403)
