from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

urlpatterns = [
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.fleet.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.booking.urls")),
    path("", include("apps.rides.urls")),
    path("", include("apps.corporate.urls")),
    path("", include("apps.charter.urls")),
    path("", include("apps.goods.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.audit.urls")),
]
