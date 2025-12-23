from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from docatho_backend.masters.models import BaseModel
from docatho_backend.medicines.models import Medicine


class Order(BaseModel):
    class Status(models.TextChoices):
        PLACED = "placed", _("Placed")
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

    order_number = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders"
    )
    # select the address used for this order
    address = models.ForeignKey(
        "users.Address",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PLACED
    )
    payment_status = models.CharField(
        max_length=32, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )

    total_mrp = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    delivery_fee = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    estimated_delivery_start = models.DateTimeField(null=True, blank=True)
    estimated_delivery_end = models.DateTimeField(null=True, blank=True)
    placed_at = models.DateTimeField(default=timezone.now)
    delivered_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ("-placed_at",)

    def __str__(self) -> str:
        return f"Order<{self.order_number}> user={self.user_id} status={self.status}"

    @transaction.atomic
    def recalc_totals(self) -> None:
        items = self.items.all()
        subtotal = Decimal("0.00")
        total_mrp = Decimal("0.00")
        for it in items:
            subtotal += it.line_total
            total_mrp += (it.mrp or it.unit_price) * it.quantity
        self.subtotal = subtotal.quantize(Decimal("0.01"))
        self.total_mrp = total_mrp.quantize(Decimal("0.01"))
        # ensure discount doesn't exceed subtotal
        discount = self.discount_amount or Decimal("0.00")
        if discount > self.subtotal:
            discount = self.subtotal
        self.discount_amount = discount.quantize(Decimal("0.01"))
        self.total = (
            self.subtotal
            + (self.delivery_fee or Decimal("0.00"))
            - self.discount_amount
        ).quantize(Decimal("0.01"))
        self.save(
            update_fields=[
                "subtotal",
                "total_mrp",
                "discount_amount",
                "total",
                "updated_at",
            ]
        )

    @transaction.atomic
    def update_status(self, new_status: str, notes: Optional[str] = None) -> None:
        """
        Update the order status with validation and automatic field updates.

        Args:
            new_status: The new status value (must be a valid Status choice)
            notes: Optional notes to add to the order

        Raises:
            ValueError: If the new_status is not a valid Status choice
        """
        # Validate status
        valid_statuses = [choice[0] for choice in self.Status.choices]
        if new_status not in valid_statuses:
            raise ValueError(
                f"Invalid status '{new_status}'. Must be one of: {', '.join(valid_statuses)}"
            )

        old_status = self.status
        self.status = new_status

        # Auto-update delivered_at when status changes to DELIVERED
        if new_status == self.Status.DELIVERED and not self.delivered_at:
            self.delivered_at = timezone.now()

        # Update notes if provided
        if notes:
            existing_notes = self.notes or ""
            if existing_notes:
                self.notes = f"{existing_notes}\n\n{notes}"
            else:
                self.notes = notes

        # Save the order
        update_fields = ["status", "updated_at"]
        if new_status == self.Status.DELIVERED and self.delivered_at:
            update_fields.append("delivered_at")
        if notes:
            update_fields.append("notes")

        self.save(update_fields=update_fields)

        # Create a log entry for status change
        try:
            OrderLog.objects.create(
                order=self,
                message=f"Status changed from {old_status} to {new_status}",
                meta={"old_status": old_status, "new_status": new_status},
            )
        except Exception:
            # Don't fail if logging fails
            pass


class OrderItem(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey(
        Medicine, on_delete=models.PROTECT, related_name="order_items"
    )
    quantity = models.PositiveIntegerField(default=1)
    # price snapshot at order time
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
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
        Order, on_delete=models.CASCADE, related_name="transactions"
    )
    provider = models.CharField(
        max_length=100, default="razorpay", editable=False
    )  # always razorpay
    payment_method = models.CharField(
        max_length=50, blank=True, null=True
    )  # e.g. card, upi, netbanking
    transaction_order_id = models.CharField(
        max_length=255, blank=True, null=True, db_index=True
    )
    razorpay_payment_id = models.CharField(
        max_length=255, blank=True, null=True, db_index=True
    )
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    succeeded = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    raw_response = models.JSONField(blank=True, null=True)

    class Meta:
        ordering = ("-paid_at",)

    def __str__(self) -> str:
        return f"Transaction<{self.pk}> order={self.order_id} amount={self.amount} succeeded={self.succeeded}"


class OrderLog(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="logs")
    message = models.TextField()
    meta = models.JSONField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"OrderLog<{self.pk}> order={self.order_id} msg={self.message[:60]}"
