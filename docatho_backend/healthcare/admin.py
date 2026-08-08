from django.contrib import admin

from docatho_backend.healthcare import models


@admin.register(models.MedicalSpecialty)
class MedicalSpecialtyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")


@admin.register(models.DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ("provider", "verification_status", "is_verified", "rating_avg")
    list_filter = ("verification_status", "is_verified")
    search_fields = ("provider__name",)


@admin.register(models.Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "doctor", "scheduled_at", "status")
    list_filter = ("status", "consultation_mode")


@admin.register(models.DiagnosticTest)
class DiagnosticTestAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "is_active")


@admin.register(models.DiagnosticBooking)
class DiagnosticBookingAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "center", "status", "total_amount")


@admin.register(models.MedicineReminder)
class MedicineReminderAdmin(admin.ModelAdmin):
    list_display = ("user", "medicine_name", "is_active")


@admin.register(models.WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("user", "medicine")


@admin.register(models.SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("subject", "user", "status")


@admin.register(models.ContentPage)
class ContentPageAdmin(admin.ModelAdmin):
    list_display = ("page_type", "title", "is_published")
