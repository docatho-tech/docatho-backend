# enum for provider type with choices as Doctor, Diagnostic Center, Chemist, etc.
from enum import Enum


class ProviderType(Enum):
    DOCTOR = "Doctor"
    DIAGNOSTIC_CENTER = "Diagnostic Center"
    LAB = "Lab"
    CHEMIST = "Chemist"
    HOME_HEALTHCARE = "Home Healthcare"
    HOSPITAL = "Hospital"
    NURSE = "Nurse"
    PHYSIOTHERAPIST = "Physiotherapist"
    OTHER = "Other"

    @classmethod
    def choices(cls):
        """Return choices usable in Django model fields as (value, label) pairs."""
        return [(member.value, member.value) for member in cls]
