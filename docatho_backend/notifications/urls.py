from rest_framework.routers import DefaultRouter

from .views import DeviceTokenViewSet
from .views import NotificationViewSet

app_name = "notifications"
router = DefaultRouter()
router.register(r"notifications", NotificationViewSet, basename="notification")
router.register(r"device-tokens", DeviceTokenViewSet, basename="device-token")

urlpatterns = router.urls
