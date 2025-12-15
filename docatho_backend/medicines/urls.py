from django.urls import path
from rest_framework.routers import DefaultRouter

from docatho_backend.medicines.views import CategoryViewset, MedicineViewset

app_name = "medicines"
router = DefaultRouter()
router.register(r"categories", CategoryViewset, basename="category")
router.register(r"", MedicineViewset, basename="medicine")

urlpatterns = router.urls
urlpatterns += [
    # Additional custom paths can be added here if needed
]
