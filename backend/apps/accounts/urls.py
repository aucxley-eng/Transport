from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.accounts.views import (
    MembershipViewSet,
    MeView,
    OrganizationViewSet,
    RegisterView,
)

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("memberships", MembershipViewSet, basename="memberships")

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("me/", MeView.as_view(), name="me"),
    path("organizations/<uuid:pk>/members/", MembershipViewSet.as_view({"get": "list", "post": "create"})),
    path("", include(router.urls)),
]