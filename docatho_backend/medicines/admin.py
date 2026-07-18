from django.contrib import admin

from docatho_backend.medicines.models import Category
from docatho_backend.medicines.models import Medicine


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "brand",
        "manufacturer",
        "price",
        "mrp",
        "stock",
        "schedule",
        "is_prescription_required",
        "is_active",
    )
    list_filter = ("schedule", "is_prescription_required", "is_active")
    search_fields = ("name", "brand", "manufacturer")
    filter_horizontal = ("category",)
