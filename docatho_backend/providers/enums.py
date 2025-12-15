# enum for provider type with choices as Doctor, Diagnostic Center, Chemist, etc.
from enum import Enum


class ProviderType(Enum):
    DOCTOR = "Doctor"
    DIAGNOSTIC_CENTER = "Diagnostic Center"
    CHEMIST = "Chemist"
    HOSPITAL = "Hospital"
    NURSE = "Nurse"
    PHYSIOTHERAPIST = "Physiotherapist"
    OTHER = "Other"

    @classmethod
    def choices(cls):
        """Return choices usable in Django model fields as (value, label) pairs."""
        return [(member.value, member.value) for member in cls]
