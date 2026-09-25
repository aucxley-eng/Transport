from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.rides.views import (
    DriverOfferViewSet,
    DriverShiftViewSet,
    RideRequestViewSet,
    ServiceZoneViewSet,
    TripViewSet,
)

router = DefaultRouter()
router.register("rides", RideRequestViewSet, basename="rides")
router.register("offers", DriverOfferViewSet, basename="driver-offers")
router.register("trips", TripViewSet, basename="trips")
router.register("driver/shifts", DriverShiftViewSet, basename="driver-shifts")
router.register("service-zones", ServiceZoneViewSet, basename="service-zones")

urlpatterns = [path("", include(router.urls))]
