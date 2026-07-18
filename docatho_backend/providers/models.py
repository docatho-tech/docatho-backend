from django.db import models

from docatho_backend.masters.models import BaseModel
from docatho_backend.providers.enums import ProviderType
from docatho_backend.users.models import User


class Provider(BaseModel):
    name = models.CharField(max_length=255)
    specialty = models.CharField(max_length=255)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="provider")

    # type Doctor, Diagnostic Center, Chemist, etc.
    provider_type = models.CharField(
        max_length=100,
        choices=ProviderType.choices(),
        default=ProviderType.CHEMIST.value,
    )

    # Payout / bank details (EP-07)
    bank_account_name = models.CharField(max_length=255, blank=True, null=True)
    bank_account_number = models.CharField(max_length=34, blank=True, null=True)
    bank_ifsc = models.CharField(max_length=20, blank=True, null=True)
    upi_id = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return self.name
