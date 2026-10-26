from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.accounts.views import (
    BranchViewSet,
    MembershipViewSet,
    MeView,
    OrganizationViewSet,
    RegisterView,
    StaffViewSet,
)

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("branches", BranchViewSet, basename="branches")
router.register("memberships", MembershipViewSet, basename="memberships")
router.register("staff", StaffViewSet, basename="staff")

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("me/", MeView.as_view(), name="me"),
    path("organizations/<uuid:pk>/members/", MembershipViewSet.as_view({"get": "list", "post": "create"})),
    path("", include(router.urls)),
]