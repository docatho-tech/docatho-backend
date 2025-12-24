from django.db import models
from docatho_backend.users.models import User
from docatho_backend.masters.models import BaseModel
from docatho_backend.providers.enums import ProviderType


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

    def __str__(self):
        return self.name
