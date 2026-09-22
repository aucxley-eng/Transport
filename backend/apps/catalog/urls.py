from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.catalog.views import PlaceViewSet, QuoteViewSet, RateCardViewSet, ServiceViewSet

router = DefaultRouter()
router.register("services", ServiceViewSet, basename="services")
router.register("places", PlaceViewSet, basename="places")
router.register("rate-cards", RateCardViewSet, basename="rate-cards")
router.register("quotes", QuoteViewSet, basename="quotes")

urlpatterns = [path("", include(router.urls))]