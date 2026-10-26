"""Writing, uploading and reading an appointment's e-prescription.

One endpoint, mounted at both a `/provider/` and an `/admin/` path, because it
is one document with one set of rules — who may touch it is decided per
appointment (its doctor, or a staff account), not per URL.

Two ways to produce it, and they are the same record:

* the doctor fills the form after the consultation and the server renders the
  PDF, or
* a doctor or an admin attaches the sheet that was written by hand, uploaded
  through `/api/uploads/` like every other document in the product.
"""

from __future__ import annotations

from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import permissions
from rest_framework import serializers
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from docatho_backend.healthcare.models import Appointment
from docatho_backend.healthcare.models import AppointmentPrescription
from docatho_backend.healthcare.models import AppointmentStatus
from docatho_backend.healthcare.models import PrescriptionSource
from docatho_backend.healthcare.prescriptions import store_prescription_pdf
from docatho_backend.masters.permissions import is_provider

#: A prescription is the outcome of a consultation, so it cannot be written
#: before one happens. `in_progress` is included on purpose: a doctor usually
#: writes it in the last minute of the call, not after hanging up.
PRESCRIBABLE = frozenset({AppointmentStatus.IN_PROGRESS, AppointmentStatus.COMPLETED})

MEDICATION_KEYS = ("type", "drug", "dose", "duration")


class AppointmentPrescriptionSerializer(serializers.ModelSerializer):
    """The form the doctor fills, and what every client reads back.

    `file_url` in, `document_url` out: the client uploads bytes to
    `/api/uploads/` and sends the address here, and reads back one address
    whether the document was rendered or attached.
    """

    file_url = serializers.CharField(write_only=True, required=False, allow_blank=True)
    document_url = serializers.SerializerMethodField()
    doctor_name = serializers.CharField(
        source="appointment.doctor.provider.name",
        read_only=True,
    )
    patient_name = serializers.CharField(
        source="appointment.patient.name",
        read_only=True,
    )

    class Meta:
        model = AppointmentPrescription
        fields = (
            "id",
            "source",
            "doctor_name",
            "patient_name",
            "bp",
            "pulse",
            "weight",
            "height",
            "complaints",
            "allergies",
            "diagnosis",
            "advice",
            "medications",
            "instructions",
            "follow_up",
            "file_url",
            "document_url",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "source", "document_url", "created_at", "updated_at")

    def get_document_url(self, obj) -> str:
        if obj.document_url:
            return obj.document_url
        if not obj.pdf:
            return ""
        request = self.context.get("request")
        url = obj.pdf.url
        return request.build_absolute_uri(url) if request else url

    def validate_medications(self, value):
        """Four named columns per row, and a row with no drug is not a row."""
        if not isinstance(value, list):
            raise serializers.ValidationError("Medications must be a list of rows.")
        rows = []
        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError(
                    "Each medication must be an object with "
                    f"{', '.join(MEDICATION_KEYS)}.",
                )
            if not str(item.get("drug") or "").strip():
                continue
            rows.append(
                {key: str(item.get(key) or "").strip() for key in MEDICATION_KEYS},
            )
        return rows


def _may_prescribe(user, appointment) -> bool:
    """The appointment's own doctor, or a staff account acting for them."""
    if user.is_staff:
        return True
    if not is_provider(user):
        return False
    return appointment.doctor.provider.user_id == user.pk


class AppointmentPrescriptionAPIView(APIView):
    """`GET/POST/DELETE /…/appointments/<id>/prescription/`.

    POST is create-or-replace rather than create-only: an appointment has one
    prescription, and a doctor correcting a dose is editing that document, not
    issuing a second one the patient would have to choose between.
    """

    permission_classes = [permissions.IsAuthenticated]

    def _appointment(self, request, appointment_id: int) -> Appointment:
        appointment = get_object_or_404(
            Appointment.objects.select_related(
                "patient",
                "doctor__provider__user",
            ),
            pk=appointment_id,
        )
        if not _may_prescribe(request.user, appointment):
            # 404, not 403: whether an appointment exists is not something an
            # unrelated account should be able to probe.
            raise Http404
        return appointment

    def get(self, request, appointment_id: int):
        appointment = self._appointment(request, appointment_id)
        prescription = getattr(appointment, "prescription", None)
        if prescription is None:
            return Response(
                {"detail": "No prescription for this appointment yet."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            AppointmentPrescriptionSerializer(
                prescription,
                context={"request": request},
            ).data,
        )

    def post(self, request, appointment_id: int):
        appointment = self._appointment(request, appointment_id)
        if appointment.status not in PRESCRIBABLE:
            return Response(
                {"detail": "A prescription can only be issued once the consultation "
                           "has started."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = getattr(appointment, "prescription", None)
        serializer = AppointmentPrescriptionSerializer(
            existing,
            data=request.data,
            partial=existing is not None,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        file_url = (serializer.validated_data.pop("file_url", "") or "").strip()

        prescription = serializer.save(
            appointment=appointment,
            issued_by=request.user,
            source=(
                PrescriptionSource.UPLOADED
                if file_url
                else PrescriptionSource.GENERATED
            ),
            document_url=file_url,
        )
        if file_url:
            # An attached sheet replaces anything previously rendered — two
            # documents on one consultation is the ambiguity this avoids.
            if prescription.pdf:
                prescription.pdf.delete(save=True)
        else:
            store_prescription_pdf(prescription)

        return Response(
            AppointmentPrescriptionSerializer(
                prescription,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED if existing is None else status.HTTP_200_OK,
        )

    def delete(self, request, appointment_id: int):
        """Withdraw a prescription issued in error.

        Staff only: a document the patient may already have acted on should be
        removed by whoever answers for that, not by whoever typed it.
        """
        appointment = self._appointment(request, appointment_id)
        if not request.user.is_staff:
            return Response(
                {"detail": "Only an administrator can withdraw a prescription."},
                status=status.HTTP_403_FORBIDDEN,
            )
        prescription = getattr(appointment, "prescription", None)
        if prescription is not None:
            prescription.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
