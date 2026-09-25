from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.goods.views import (
    FreightTripViewSet,
    ManifestViewSet,
    PackageViewSet,
    ShipmentExceptionViewSet,
    ShipmentViewSet,
)

router = DefaultRouter()
router.register("shipments", ShipmentViewSet, basename="shipment")
router.register("packages", PackageViewSet, basename="package")
router.register("shipment-exceptions", ShipmentExceptionViewSet, basename="shipment-exception")
router.register("freight-trips", FreightTripViewSet, basename="freight-trip")
router.register("manifests", ManifestViewSet, basename="manifest")

urlpatterns = [path("", include(router.urls))]
