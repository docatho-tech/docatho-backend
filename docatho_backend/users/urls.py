from django.urls import path

from .views import RegisterView, user_detail_view
from .views import user_redirect_view
from .views import user_update_view
from docatho_backend.users.views import SendOTPApiView, VerifyOtpAPIView, RegisterView
from .views import UpdateProfileAPIView

app_name = "users"
urlpatterns = [
    path("~redirect/", view=user_redirect_view, name="redirect"),
    path("~update/", view=user_update_view, name="update"),
    path("<int:pk>/", view=user_detail_view, name="detail"),
    path("send-otp/", SendOTPApiView.as_view(), name="send-otp"),
    path("verify-otp/", VerifyOtpAPIView.as_view(), name="verify-otp"),
    path("register/", RegisterView.as_view(), name="verify-otp"),
    path("update-profile/", UpdateProfileAPIView.as_view(), name="update-profile"),
]
