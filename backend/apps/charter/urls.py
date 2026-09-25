from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.charter.views import CharterAvailabilityView, HireRequestViewSet

router = DefaultRouter()
router.register("charter/requests", HireRequestViewSet, basename="charter-requests")

urlpatterns = [
    path("charter/availability/", CharterAvailabilityView.as_view(), name="charter-availability"),
    path("", include(router.urls)),
]
