from django.contrib import admin

from docatho_backend.medicines.models import Category, Medicine


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "image_url", "created_at", "updated_at")
    search_fields = ("name",)


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "manufacturer",
        "price",
        "mrp",
        "stock",
        "created_at",
        "updated_at",
    )
    search_fields = ("name", "manufacturer")
