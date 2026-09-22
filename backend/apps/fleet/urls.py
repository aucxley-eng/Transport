from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.fleet.views import DocumentViewSet, DriverViewSet, VehicleViewSet

router = DefaultRouter()
router.register("vehicles", VehicleViewSet, basename="vehicles")
router.register("drivers", DriverViewSet, basename="drivers")
router.register("documents", DocumentViewSet, basename="documents")

urlpatterns = [path("", include(router.urls))]