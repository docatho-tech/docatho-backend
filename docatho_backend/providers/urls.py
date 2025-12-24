from django.urls import path

from .views import (
    SendOTPAPIView,
    VerifyOTPAPIView,
    ChemistOrderListAPIView,
    ChemistOrderUpdateAPIView,
)

app_name = "providers"
urlpatterns = [
    path("send-otp/", SendOTPAPIView.as_view(), name="send-otp"),
    path("verify-otp/", VerifyOTPAPIView.as_view(), name="verify-otp"),
    path(
        "chemist-order-list/",
        ChemistOrderListAPIView.as_view(),
        name="chemist-order-list",
    ),
    path(
        "chemist-order-update/<int:pk>/",
        ChemistOrderUpdateAPIView.as_view(),
        name="chemist-order-update",
    ),
]
