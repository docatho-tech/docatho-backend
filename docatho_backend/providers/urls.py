from django.urls import path

from .views import (
    SendOTPAPIView,
    UserDetailAPIView,
    VerifyOTPAPIView,
    ChemistOrderListAPIView,
    ChemistOrderUpdateAPIView,
    OrderDetailAPIView,
)

app_name = "providers"
urlpatterns = [
    path("user-detail/", UserDetailAPIView.as_view(), name="user-detail"),
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
    path(
        "order-detail/<int:pk>/",
        OrderDetailAPIView.as_view(),
        name="order-detail",
    ),
]
