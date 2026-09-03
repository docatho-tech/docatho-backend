"""One image upload endpoint, shared by every admin screen that shows a picture.

The product addresses images by URL everywhere already — ``Medicine.image_url``,
``Category.image_url``, ``DoctorProfile.clinic_images`` — so a client needs a way
to turn a chosen file into one of those URLs. It posts the file here, gets an
address back, and puts that address in whatever field it was editing.

Kept out of any one app because doctors, diagnostic tests and medicines all
reach for it; a view needs no app of its own.
"""

import uuid
from pathlib import Path

from django.core.files.storage import default_storage
from rest_framework import status
from rest_framework.parsers import FormParser
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from docatho_backend.masters.permissions import IsAdmin

# Raster formats a browser will render inline, plus PDF for the verification
# documents — a medical licence is rarely a photograph. SVG is deliberately
# absent: it can carry script, and these files are served from our own domain.
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}
ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
}
MAX_BYTES = 5 * 1024 * 1024


class ImageUploadView(APIView):
    """POST a file as multipart ``file``; get back ``{"url": ...}``."""

    permission_classes = [IsAdmin]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"detail": "No file was sent under the key 'file'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        suffix = Path(upload.name or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES or upload.content_type not in ALLOWED_TYPES:
            return Response(
                {
                    "detail": (
                        "Unsupported file type. Use JPG, PNG, WebP, GIF or PDF."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if upload.size > MAX_BYTES:
            return Response(
                {"detail": "That file is larger than 5 MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Never trust the client's filename as a path: it decides where the
        # bytes land. A random name keeps uploads from colliding or escaping
        # the prefix, and the extension is one we just validated.
        name = f"uploads/{uuid.uuid4().hex}{suffix}"
        saved = default_storage.save(name, upload)

        # Always absolute. S3 storage answers with a full URL, but local disk
        # answers "/media/..." — and the clients are served from other origins
        # (the dashboard on Amplify or :5173), so a relative path would resolve
        # against *their* host and 404. build_absolute_uri leaves an already
        # absolute URL alone, so this is right under either backend.
        return Response(
            {"url": request.build_absolute_uri(default_storage.url(saved))},
            status=status.HTTP_201_CREATED,
        )
