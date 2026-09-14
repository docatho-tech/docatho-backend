from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from docatho_backend.masters.models import BaseModel
from docatho_backend.medicines.models import Medicine

TWO_PLACES = Decimal("0.01")


def _commission_percent() -> Decimal:
    return Decimal(str(getattr(settings, "PHARMACY_COMMISSION_PERCENT", 10.0)))


class Prescription(BaseModel):
    """A prescription document uploaded by a patient.

    Required to check out any medicine with ``is_prescription_required=True``.
    Stored via the default storage backend (local in dev, S3 in production).
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending review")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="prescriptions",
    )
    image = models.FileField(upload_to="prescriptions/%Y/%m/")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    notes = models.TextField(blank=True, null=True)

    # Who cleared this document, and when. Approving a Schedule H/H1/X
    # prescription is a regulated decision made on a shared ops login; the note
    # was stored but the decision was attributable to nobody.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="prescriptions_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Prescription<{self.pk}> user={self.user_id} status={self.status}"


class Order(BaseModel):
    class Status(models.TextChoices):
        PLACED = "placed", _("Placed")
        # Provider fulfilment lifecycle (EP-06)
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        PACKED = "packed", _("Packed")
        # Retained legacy states (still valid transitions)
        CONFIRMED = "confirmed", _("Confirmed")
        PROCESSING = "processing", _("Processing")
        OUT_FOR_DELIVERY = "out_for_delivery", _("Out for delivery")
        DELIVERED = "delivered", _("Delivered")
        CANCELLED = "cancelled", _("Cancelled")
        RETURNED = "returned", _("Returned")

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        REFUNDED = "refunded", _("Refunded")

    class PaymentMethod(models.TextChoices):
        ONLINE = "online", _("Online (Razorpay)")
        COD = "cod", _("Cash on delivery")

    # Statuses that free previously reserved stock.
    STOCK_RELEASING_STATUSES = frozenset({"rejected", "cancelled", "returned"})

    order_number = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    # select the address used for this order
    address = models.ForeignKey(
        "users.Address",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Fulfilling pharmacy/provider (EP-06/EP-10 assignment).
    assigned_provider = models.ForeignKey(
        "providers.Provider",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    # Prescription attached at checkout when any item requires one.
    prescription = models.ForeignKey(
        Prescription,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PLACED,
    )
    payment_status = models.CharField(
        max_length=32,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    payment_method = models.CharField(
        max_length=16,
        choices=PaymentMethod.choices,
        default=PaymentMethod.ONLINE,
    )

    total_mrp = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    delivery_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    # Money split (EP-11): platform commission vs. provider payout.
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    commission_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    provider_earning = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    # Who is carrying the order. The pharmacy app shows both on every in-transit
    # and delivered row ("Rider : RDR-218", "I. Prakash"), and the customer asks
    # for the code, not the name, when they ring about a late delivery.
    rider_name = models.CharField(max_length=120, blank=True, default="")
    rider_code = models.CharField(max_length=40, blank=True, default="")

    stock_reserved = models.BooleanField(default=False)
    estimated_delivery_mins = models.IntegerField(default=0)
    placed_at = models.DateTimeField(default=timezone.now)
    delivered_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ("-placed_at",)

    def __str__(self) -> str:
        return f"Order<{self.order_number}> user={self.user_id} status={self.status}"

    @property
    def requires_prescription(self) -> bool:
        return self.items.filter(prescription_required=True).exists()

    @transaction.atomic
    def recalc_totals(self) -> None:
        items = self.items.all()
        subtotal = Decimal("0.00")
        total_mrp = Decimal("0.00")
        for it in items:
            subtotal += it.line_total
            total_mrp += (it.mrp or it.unit_price) * it.quantity
        self.subtotal = subtotal.quantize(TWO_PLACES)
        self.total_mrp = total_mrp.quantize(TWO_PLACES)
        # ensure discount doesn't exceed subtotal
        discount = self.discount_amount or Decimal("0.00")
        discount = min(discount, self.subtotal)
        self.discount_amount = discount.quantize(TWO_PLACES)
        self.total = (
            self.subtotal
            + (self.delivery_fee or Decimal("0.00"))
            - self.discount_amount
        ).quantize(TWO_PLACES)
        self.save(
            update_fields=[
                "subtotal",
                "total_mrp",
                "discount_amount",
                "total",
                "updated_at",
            ],
        )

    def compute_commission(self, rate: Decimal | None = None) -> None:
        """Split the item subtotal into platform commission and provider payout."""
        if rate is None:
            assigned = getattr(self, "assigned_provider", None)
            if assigned is not None and assigned.commission_percent is not None:
                rate = assigned.commission_percent
            else:
                rate = self.commission_rate or _commission_percent()
        rate = Decimal(rate)
        self.commission_rate = rate.quantize(TWO_PLACES)
        self.commission_amount = (self.subtotal * rate / Decimal("100")).quantize(
            TWO_PLACES,
        )
        self.provider_earning = (self.subtotal - self.commission_amount).quantize(
            TWO_PLACES,
        )
        self.save(
            update_fields=[
                "commission_rate",
                "commission_amount",
                "provider_earning",
                "updated_at",
            ],
        )

    @transaction.atomic
    def reserve_stock(self) -> None:
        """Deduct ordered quantities from medicine stock.

        Idempotent: does nothing if already reserved. Raises ``ValueError``
        listing any items whose stock is insufficient (nothing is deducted in
        that case).
        """
        if self.stock_reserved:
            return
        items = list(self.items.select_related("medicine"))
        shortages = [
            it.medicine.name for it in items if it.medicine.stock < it.quantity
        ]
        if shortages:
            raise ValueError(f"Out of stock: {', '.join(shortages)}")
        for it in items:
            Medicine.objects.filter(pk=it.medicine_id).update(
                stock=models.F("stock") - it.quantity,
            )
        self.stock_reserved = True
        self.save(update_fields=["stock_reserved", "updated_at"])

    @transaction.atomic
    def release_stock(self) -> None:
        """Return previously reserved quantities to stock. Idempotent."""
        if not self.stock_reserved:
            return
        for it in self.items.select_related("medicine"):
            Medicine.objects.filter(pk=it.medicine_id).update(
                stock=models.F("stock") + it.quantity,
            )
        self.stock_reserved = False
        self.save(update_fields=["stock_reserved", "updated_at"])

    @transaction.atomic
    def update_status(self, new_status: str, notes: str | None = None) -> None:
        """Update the order status with validation and side effects.

        Raises ``ValueError`` if ``new_status`` is not a valid choice.
        """
        valid_statuses = [choice[0] for choice in self.Status.choices]
        if new_status not in valid_statuses:
            raise ValueError(
                f"Invalid status '{new_status}'. Must be one of: "
                f"{', '.join(valid_statuses)}",
            )

        old_status = self.status
        self.status = new_status

        if new_status == self.Status.DELIVERED and not self.delivered_at:
            self.delivered_at = timezone.now()

        if notes:
            existing_notes = self.notes or ""
            self.notes = f"{existing_notes}\n\n{notes}" if existing_notes else notes

        update_fields = ["status", "updated_at"]
        if new_status == self.Status.DELIVERED and self.delivered_at:
            update_fields.append("delivered_at")
        if notes:
            update_fields.append("notes")

        self.save(update_fields=update_fields)

        # Return stock to inventory when an order is rejected/cancelled/returned.
        if new_status in self.STOCK_RELEASING_STATUSES:
            self.release_stock()

        try:
            OrderLog.objects.create(
                order=self,
                message=f"Status changed from {old_status} to {new_status}",
                meta={"old_status": old_status, "new_status": new_status},
            )
        except Exception:
            # Don't fail the status change if logging fails
            pass


class OrderItem(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey(
        Medicine,
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    quantity = models.PositiveIntegerField(default=1)
    # price snapshot at order time
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    mrp = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    prescription_required = models.BooleanField(default=False)

    class Meta:
        ordering = ("-updated_at",)
        unique_together = ("order", "medicine")

    def __str__(self) -> str:
        return f"OrderItem<{self.pk}> {self.medicine_id} x{self.quantity}"

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price or Decimal("0.00")) * Decimal(self.quantity)

    def save(self, *args, **kwargs):
        if not self.unit_price or self.unit_price == Decimal("0.00"):
            self.unit_price = getattr(self.medicine, "price", Decimal("0.00"))
        if not self.mrp or self.mrp == Decimal("0.00"):
            self.mrp = getattr(self.medicine, "mrp", self.unit_price)
        super().save(*args, **kwargs)
        try:
            self.order.recalc_totals()
        except Exception:
            pass


class Transaction(BaseModel):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    provider = models.CharField(
        max_length=100,
        default="razorpay",
        editable=False,
    )  # always razorpay
    payment_method = models.CharField(
        max_length=50,
        blank=True,
        null=True,
    )  # e.g. card, upi, netbanking
    transaction_order_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
    )
    razorpay_payment_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
    )
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    succeeded = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    raw_response = models.JSONField(blank=True, null=True)

    class Meta:
        ordering = ("-paid_at",)

    def __str__(self) -> str:
        return (
            f"Transaction<{self.pk}> order={self.order_id} "
            f"amount={self.amount} succeeded={self.succeeded}"
        )


class PayoutStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SETTLED = "settled", "Settled"
    FAILED = "failed", "Failed"


class Payout(BaseModel):
    """
    Money actually moved to a partner.

    The Settlements view is a *live aggregate* of what a partner has earned and
    not yet been paid; this is the ledger of transfers already initiated, which
    an aggregate cannot reconstruct — two payouts of ₹50,000 and one of
    ₹1,00,000 sum to the same figure as one of ₹2,00,000, and only this table
    knows which happened. `settled_at` is null until the bank confirms.
    """

    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.PROTECT,
        related_name="payouts",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=16,
        choices=PayoutStatus.choices,
        default=PayoutStatus.PENDING,
    )
    initiated_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    reference = models.CharField(max_length=64, blank=True, default="")
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-initiated_at",)

    def __str__(self) -> str:
        return f"Payout<{self.pk}> provider={self.provider_id} amount={self.amount}"


class OrderLog(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="logs")
    message = models.TextField()
    meta = models.JSONField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"OrderLog<{self.pk}> order={self.order_id} msg={self.message[:60]}"


class Invoice(BaseModel):
    """A generated invoice for an order (EP-03 download, EP-10 admin)."""

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="invoice",
    )
    invoice_number = models.CharField(max_length=64, unique=True, db_index=True)
    pdf = models.FileField(upload_to="invoices/%Y/%m/", blank=True, null=True)
    issued_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-issued_at",)

    def __str__(self) -> str:
        return f"Invoice<{self.invoice_number}> order={self.order_id}"
