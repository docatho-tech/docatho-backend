from django.urls import path

from .views import AdminProviderDetailAPIView
from .views import AdminProviderListCreateAPIView
from .views import ChemistOrderListAPIView
from .views import ChemistOrderUpdateAPIView
from .views import OrderDetailAPIView
from .views import OrderInvoiceAPIView
from .views import ProviderBankAPIView
from .views import ProviderEarningsAPIView
from .views import ProviderProfileAPIView
from .views import SendOTPAPIView
from .views import UserDetailAPIView
from .views import VerifyOTPAPIView

app_name = "providers"
urlpatterns = [
    path(
        "admin/list/",
        AdminProviderListCreateAPIView.as_view(),
        name="admin-provider-list",
    ),
    path(
        "admin/<int:pk>/",
        AdminProviderDetailAPIView.as_view(),
        name="admin-provider-detail",
    ),
    path("user-detail/", UserDetailAPIView.as_view(), name="user-detail"),
    path("send-otp/", SendOTPAPIView.as_view(), name="send-otp"),
    path("verify-otp/", VerifyOTPAPIView.as_view(), name="verify-otp"),
    path("profile/", ProviderProfileAPIView.as_view(), name="profile"),
    path("bank/", ProviderBankAPIView.as_view(), name="bank"),
    path("earnings/", ProviderEarningsAPIView.as_view(), name="earnings"),
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
    path(
        "order-invoice/<int:pk>/",
        OrderInvoiceAPIView.as_view(),
        name="order-invoice",
    ),
]
