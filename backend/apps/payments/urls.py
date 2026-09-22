from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.payments.views import (
    LedgerEntryListView,
    PaymentAttemptViewSet,
    ProviderCallbackView,
)

router = DefaultRouter()
router.register("payments", PaymentAttemptViewSet, basename="payments")

urlpatterns = [
    path("", include(router.urls)),
    path("ledger/entries/", LedgerEntryListView.as_view(), name="ledger-entries"),
    path("webhooks/<slug:provider>/", ProviderCallbackView.as_view(), name="provider-callback"),
]