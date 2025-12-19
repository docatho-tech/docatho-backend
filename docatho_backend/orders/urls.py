from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import OrderViewSet, razorpay_webhook

router = DefaultRouter()
router.register(r"orders", OrderViewSet, basename="orders")

urlpatterns = [
    path("", include(router.urls)),
    path("webhooks/razorpay/", razorpay_webhook, name="razorpay-webhook"),
]
