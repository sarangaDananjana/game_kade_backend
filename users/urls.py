from django.urls import path
from .views import SendOTPView, VerifyOTPView, DeleteAccountView, AppVersionView, ProfileView, RateLimitedTokenRefreshView

urlpatterns = [
    path('send-otp/', SendOTPView.as_view(), name='send-otp'),
    path('verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    path('token/refresh/', RateLimitedTokenRefreshView.as_view(), name='token_refresh'),
    path('delete-account/', DeleteAccountView.as_view(), name='delete-account-api'),
    path('app-version/', AppVersionView.as_view(), name='app-version'),
    path('profile/', ProfileView.as_view(), name='profile'),
]
