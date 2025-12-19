from django.contrib import admin


from docatho_backend.orders.models import Order, OrderItem


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "created_at", "updated_at")
    search_fields = ("user__email", "user__first_name", "user__last_name", "id")
    list_filter = ("status", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "medicine", "quantity", "unit_price", "line_total")
    search_fields = ("order__id", "medicine__name")
    ordering = ("-id",)


