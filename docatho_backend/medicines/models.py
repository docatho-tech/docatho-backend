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
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    stock = models.PositiveIntegerField(default=0)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    schedule = models.CharField(
        max_length=4,
        choices=DrugSchedule,
        default=DrugSchedule.OTC,
        help_text="Drug schedule classification; H/H1/X require a prescription.",
    )
    is_prescription_required = models.BooleanField(default=False)
    # How a quantity of this medicine is counted, so an order line reads
    # "30 Caps" and "15 Tabs" rather than "30 Nos" and "15 Nos". Free text with
    # a default rather than choices: a pack unit is a label on a line, and a new
    # one (sachets, drops, a 200 ml bottle) should not need a migration.
    pack_unit = models.CharField(max_length=16, blank=True, default="Nos")
    # The SKU a pharmacy reads off its own shelf label — "MED-004" on every
    # catalogue row. Not unique: two catalogue entries for the same drug at
    # different strengths legitimately share one.
    code = models.CharField(max_length=32, blank=True, default="")
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


class ProviderMedicine(BaseModel):
    """What one pharmacy charges for a catalogue medicine, and whether it stocks it.

    The catalogue itself is shared — every pharmacy dispenses the same
    Paracetamol — so price and availability cannot live on :class:`Medicine`.
    They are a pharmacy's answer about a medicine, which is a row of its own.

    A medicine with no row here is simply one the pharmacy has not priced yet;
    the catalogue screen shows it at the list price and unavailable, rather than
    hiding it, because "not stocked" is a thing the partner has to be able to
    change.
    """

    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        related_name="medicine_listings",
    )
    medicine = models.ForeignKey(
        Medicine,
        on_delete=models.CASCADE,
        related_name="provider_listings",
    )
    # What the platform pays the pharmacy, against the medicine's MRP.
    partner_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    is_available = models.BooleanField(default=True)

    class Meta:
        unique_together = [("provider", "medicine")]
        ordering = ("medicine__name",)

    def __str__(self) -> str:
        return f"ProviderMedicine<{self.pk}> provider={self.provider_id} medicine={self.medicine_id}"
