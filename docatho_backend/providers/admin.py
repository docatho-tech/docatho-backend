from django.contrib import admin

from docatho_backend.providers.models import Provider


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "specialty", "provider_type"]
    search_fields = ["name", "specialty"]
    list_filter = ["provider_type"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]
    fields = [
        "name",
        "specialty",
        "provider_type",
        "user",
        "bank_account_name",
        "bank_account_number",
        "bank_ifsc",
        "upi_id",
    ]
    raw_id_fields = ["user"]
