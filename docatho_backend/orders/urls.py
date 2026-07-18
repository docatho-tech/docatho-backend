from django.urls import include
from django.urls import path
from rest_framework.routers import DefaultRouter

from .analytics import ProviderPayoutSummaryView
from .analytics import RevenueExportView
from .analytics import RevenueSummaryView
from .analytics import SalesAnalyticsView
from .views import AdminOrderList
from .views import OrderViewSet
from .views import PrescriptionViewSet
from .views import TransactionListView
from .views import razorpay_webhook

router = DefaultRouter()
router.register(r"orders", OrderViewSet, basename="orders")
router.register(r"prescriptions", PrescriptionViewSet, basename="prescriptions")
router.register(r"admin/orders", AdminOrderList, basename="admin-orders")
router.register(r"transactions", TransactionListView, basename="transactions")

urlpatterns = [
    path("", include(router.urls)),
    path("webhooks/razorpay/", razorpay_webhook, name="razorpay-webhook"),
    path("analytics/revenue/", RevenueSummaryView.as_view(), name="analytics-revenue"),
    path(
        "analytics/revenue/export/",
        RevenueExportView.as_view(),
        name="analytics-revenue-export",
    ),
    path("analytics/sales/", SalesAnalyticsView.as_view(), name="analytics-sales"),
    path(
        "analytics/payouts/",
        ProviderPayoutSummaryView.as_view(),
        name="analytics-payouts",
    ),
]
