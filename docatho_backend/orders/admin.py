from django.contrib import admin

from docatho_backend.orders.models import Invoice
from docatho_backend.orders.models import Order
from docatho_backend.orders.models import OrderItem
from docatho_backend.orders.models import OrderLog
from docatho_backend.orders.models import Prescription
from docatho_backend.orders.models import Transaction


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("line_total",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "user",
        "status",
        "payment_status",
        "payment_method",
        "assigned_provider",
        "total",
        "placed_at",
    )
    search_fields = ("order_number", "user__name", "user__phone")
    list_filter = ("status", "payment_status", "payment_method", "placed_at")
    ordering = ("-placed_at",)
    inlines = [OrderItemInline]
    raw_id_fields = ("user", "assigned_provider", "address", "prescription")


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "medicine", "quantity", "unit_price", "line_total")
    search_fields = ("order__order_number", "medicine__name")
    ordering = ("-id",)


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("user__name", "user__phone")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "amount", "succeeded", "paid_at")
    list_filter = ("succeeded", "provider")
    search_fields = (
        "order__order_number",
        "razorpay_payment_id",
        "transaction_order_id",
    )


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "order", "issued_at")
    search_fields = ("invoice_number", "order__order_number")


@admin.register(OrderLog)
class OrderLogAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "message", "created_at")
    search_fields = ("order__order_number",)
