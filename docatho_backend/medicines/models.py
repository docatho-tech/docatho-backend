from decimal import Decimal

from django.db import models

from docatho_backend.masters.models import BaseModel


class Category(BaseModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    image_url = models.URLField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ("name",)

    def __str__(self):
        return self.name


class DrugSchedule(models.TextChoices):
    """Indian drug schedule classification (Drugs & Cosmetics Rules)."""

    OTC = "OTC", "Over the counter"
    H = "H", "Schedule H"
    H1 = "H1", "Schedule H1"
    X = "X", "Schedule X"


# Schedules that legally require a prescription to dispense.
RX_REQUIRED_SCHEDULES = {DrugSchedule.H, DrugSchedule.H1, DrugSchedule.X}


class Medicine(BaseModel):
    name = models.CharField(max_length=255)
    brand = models.CharField(max_length=255, blank=True, null=True)
    category = models.ManyToManyField(Category, related_name="medicines")
    content = models.TextField(blank=True, null=True)
    image_url = models.URLField(blank=True, null=True)
    manufacturer = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
    )
    stock = models.PositiveIntegerField(default=0)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    schedule = models.CharField(
        max_length=4,
        choices=DrugSchedule.choices,
        default=DrugSchedule.OTC,
        help_text="Drug schedule classification; H/H1/X require a prescription.",
    )
    is_prescription_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    @property
    def in_stock(self) -> bool:
        return self.stock > 0

    def save(self, *args, **kwargs):
        # A scheduled drug (H/H1/X) always requires a prescription; enforce so
        # the Rx checkout gate can never be bypassed by a mis-set flag.
        if self.schedule in RX_REQUIRED_SCHEDULES:
            self.is_prescription_required = True
        super().save(*args, **kwargs)
