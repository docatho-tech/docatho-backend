from django.contrib import admin

from docatho_backend.providers.models import Provider

@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "specialty", "provider_type"]
    search_fields = ["name", "specialty"]
    list_filter = ["provider_type"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]
    fields = ["name", "specialty", "provider_type", "user"]
    autocomplete_fields = ["user"]
