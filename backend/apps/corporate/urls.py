from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.corporate.views import (
    CorporateMemberViewSet,
    CorporatePolicyViewSet,
    CostCenterViewSet,
    ShuttleRunViewSet,
    ShuttleTemplateViewSet,
    TravelRequestViewSet,
)

router = DefaultRouter()
router.register("corporate/policy", CorporatePolicyViewSet, basename="corporate-policy")
router.register("corporate/cost-centers", CostCenterViewSet, basename="corporate-cost-centers")
router.register("corporate/members", CorporateMemberViewSet, basename="corporate-members")
router.register("corporate/requests", TravelRequestViewSet, basename="corporate-requests")
router.register(
    "corporate/shuttle-templates", ShuttleTemplateViewSet, basename="corporate-shuttle-templates"
)
router.register("corporate/shuttle-runs", ShuttleRunViewSet, basename="corporate-shuttle-runs")

urlpatterns = [path("", include(router.urls))]
