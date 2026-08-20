from django.urls import include
from django.urls import path
from rest_framework.routers import DefaultRouter

from docatho_backend.healthcare import views

router = DefaultRouter()
router.register("appointments", views.AppointmentViewSet, basename="appointment")
router.register("diagnostic-tests", views.DiagnosticTestViewSet, basename="diagnostic-test")
router.register("diagnostic-categories", views.DiagnosticTestCategoryViewSet, basename="diagnostic-category")
router.register("diagnostic-bookings", views.DiagnosticBookingViewSet, basename="diagnostic-booking")
router.register("reminders", views.MedicineReminderViewSet, basename="reminder")
router.register("wishlist", views.WishlistViewSet, basename="wishlist")
router.register("support-tickets", views.SupportTicketViewSet, basename="support-ticket")
router.register("content", views.ContentPageViewSet, basename="content")
router.register("specialties", views.MedicalSpecialtyViewSet, basename="specialty")
router.register("admin/prescriptions", views.AdminPrescriptionViewSet, basename="admin-prescription")
router.register("admin/availability", views.AdminDoctorAvailabilityViewSet, basename="admin-availability")

urlpatterns = [
    path("doctors/", views.DoctorListAPIView.as_view(), name="doctor-list"),
    path("doctors/<int:provider_id>/", views.DoctorDetailAPIView.as_view(), name="doctor-detail"),
    path("saved-doctors/", views.SavedDoctorAPIView.as_view(), name="saved-doctors"),
    path("ai/chat/", views.AIChatAPIView.as_view(), name="ai-chat"),
    path("ai/prescription-analysis/", views.AIPrescriptionAnalysisAPIView.as_view(), name="ai-prescription"),
    path("provider/doctor-profile/", views.ProviderDoctorProfileAPIView.as_view(), name="provider-doctor-profile"),
    path("provider/availability/", views.ProviderAvailabilityAPIView.as_view(), name="provider-availability"),
    path("provider/appointments/", views.ProviderAppointmentListAPIView.as_view(), name="provider-appointments"),
    path("provider/appointments/<int:appointment_id>/video-token/", views.ProviderAppointmentVideoTokenAPIView.as_view(), name="provider-appointment-video-token"),
    path("admin/dashboard-stats/", views.AdminDashboardStatsAPIView.as_view(), name="admin-dashboard-stats"),
    path("admin/patients/", views.AdminPatientListAPIView.as_view(), name="admin-patients"),
    path("admin/patients/<int:pk>/", views.AdminPatientDetailAPIView.as_view(), name="admin-patient-detail"),
    path("admin/doctors/", views.AdminDoctorListAPIView.as_view(), name="admin-doctors"),
    path("admin/doctors/<int:pk>/", views.AdminDoctorDetailAPIView.as_view(), name="admin-doctor-detail"),
    path("admin/doctors/<int:pk>/verify/", views.AdminDoctorVerificationAPIView.as_view(), name="admin-doctor-verify"),
    path("admin/diagnostic-bookings/", views.AdminDiagnosticBookingViewSet.as_view({"get": "list"}), name="admin-diagnostic-bookings"),
    path("admin/diagnostic-bookings/<int:pk>/", views.AdminDiagnosticBookingViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}), name="admin-diagnostic-booking-detail"),
    path("", include(router.urls)),
]
