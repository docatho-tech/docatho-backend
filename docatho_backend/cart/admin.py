from django.contrib import admin

from docatho_backend.cart.models import Cart
from docatho_backend.cart.models import CartItem


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "total", "created_at", "updated_at")
    search_fields = ("user__name", "user__phone", "id")
    list_filter = ("created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cart", "medicine", "quantity", "unit_price", "line_total")
    search_fields = ("cart__id", "medicine__name")
    ordering = ("-id",)
