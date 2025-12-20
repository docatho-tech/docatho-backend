from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import OrderViewSet, razorpay_webhook, AdminOrderList

router = DefaultRouter()
router.register(r"orders", OrderViewSet, basename="orders")
router.register(r"admin/orders", AdminOrderList, basename="admin-orders")

urlpatterns = [
    path("", include(router.urls)),
    path("webhooks/razorpay/", razorpay_webhook, name="razorpay-webhook"),
]
