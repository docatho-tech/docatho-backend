from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from docatho_backend.masters.models import BaseModel
from docatho_backend.medicines.models import Medicine


class Cart(BaseModel):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="carts"
    )

    # Address model assumed at users.Address; change the string if different.
    address = models.ForeignKey(
        "users.Address",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    total_mrp = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    # discount_type: 'fixed' applies absolute amount, 'percent' applies percent to subtotal
    DISCOUNT_FIXED = "fixed"
    DISCOUNT_PERCENT = "percent"
    DISCOUNT_CHOICES = ((DISCOUNT_FIXED, "Fixed"), (DISCOUNT_PERCENT, "Percent"))
    discount_type = models.CharField(
        max_length=16, choices=DISCOUNT_CHOICES, default=DISCOUNT_FIXED
    )

    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return f"Cart<{self.pk}> user={self.user_id} status={self.status}"

    @transaction.atomic
    def add_item(self, medicine: Medicine, quantity: int = 1) -> "CartItem":
        print(medicine, quantity)
        if int(quantity) < 1:
            raise ValueError("quantity must be >= 1")
        item, created = CartItem.objects.get_or_create(
            cart=self,
            medicine=medicine,
            defaults={
                "quantity": quantity,
                "unit_price": getattr(medicine, "price", Decimal("0.00")),
                "mrp": getattr(
                    medicine, "mrp", getattr(medicine, "price", Decimal("0.00"))
                ),
            },
        )
        item.quantity = item.quantity + quantity
        # refresh unit prices from medicine snapshot
        item.unit_price = getattr(medicine, "price", item.unit_price) or Decimal("0.00")
        item.mrp = getattr(medicine, "mrp", item.mrp) or item.unit_price
        item.save()
        self.recalculate()
        return item

    @transaction.atomic
    def update_item_quantity(
        self, medicine: Medicine, quantity: int
    ) -> Optional["CartItem"]:
        try:
            item = CartItem.objects.get(cart=self, medicine=medicine)
        except CartItem.DoesNotExist:
            return None
        if quantity <= 0:
            item.delete()
        else:
            item.quantity = quantity
            item.save()
        self.recalculate()
        return item

    @transaction.atomic
    def remove_item(self, medicine: Medicine) -> None:
        CartItem.objects.filter(cart=self, medicine=medicine).delete()
        self.recalculate()

    @transaction.atomic
    def clear(self) -> None:
        CartItem.objects.filter(cart=self).delete()
        self.recalculate()

    def recalculate(self) -> None:
        items = CartItem.objects.filter(cart=self)
        subtotal = Decimal("0.00")
        total_mrp = Decimal("0.00")
        for it in items:
            subtotal += it.line_total
            total_mrp += (it.mrp or it.unit_price) * it.quantity
        self.subtotal = subtotal.quantize(Decimal("0.01"))
        self.total_mrp = total_mrp.quantize(Decimal("0.01"))
        # compute discount
        if self.discount_type == self.DISCOUNT_PERCENT:
            # discount_amount field is used as percent value (0-100)
            try:
                pct = Decimal(self.discount_amount)
                discount = (self.subtotal * (pct / Decimal("100"))).quantize(
                    Decimal("0.01")
                )
            except Exception:
                discount = Decimal("0.00")
        else:
            discount = Decimal(self.discount_amount or Decimal("0.00")).quantize(
                Decimal("0.01")
            )
        # ensure discount is not > subtotal
        if discount > self.subtotal:
            discount = self.subtotal
        self.discount_amount = discount
        self.total = (self.subtotal - discount).quantize(Decimal("0.01"))
        # persist totals
        self.save(
            update_fields=[
                "subtotal",
                "total_mrp",
                "discount_amount",
                "total",
                "updated_at",
            ]
        )


class CartItem(BaseModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey(
        Medicine, on_delete=models.PROTECT, related_name="cart_items"
    )

    quantity = models.PositiveIntegerField(default=1)
    # snapshot prices so changes in product don't affect historical cart rows
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    mrp = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        unique_together = ("cart", "medicine")
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return f"CartItem<{self.pk}> {self.medicine_id} x{self.quantity}"

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price or Decimal("0.00")) * Decimal(self.quantity)

    @property
    def is_out_of_stock(self) -> bool:
        stock = getattr(self.medicine, "stock", None)
        if stock is None:
            # unknown stock -> assume available
            return False
        try:
            return int(stock) < int(self.quantity)
        except Exception:
            return False

    def save(self, *args, **kwargs):
        if self.quantity < 1:
            raise ValueError("quantity must be >= 1")
        super().save(*args, **kwargs)
