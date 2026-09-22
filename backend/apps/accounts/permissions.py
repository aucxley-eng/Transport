from rest_framework import permissions

from apps.accounts.models import Role, organization_ids_for


class IsAuthenticated(permissions.IsAuthenticated):
    pass


class IsOrganizationMember(permissions.BasePermission):
    """Authenticated user must have an active membership in the target
    organization (if one is specified on the view or in the URL)."""

    message = "You are not a member of this organization."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return True

    def has_object_permission(self, request, view, obj):
        org = getattr(obj, "organization", None)
        if org is None:
            return True
        org_id = org.pk if hasattr(org, "pk") else org
        return org_id in organization_ids_for(request.user)


class IsPlatformAdmin(permissions.BasePermission):
    message = "Platform administrator privileges required."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        return organization_ids_for(user) and (
            user.memberships.filter(role=Role.PLATFORM_ADMIN, is_active=True).exists()
        )


class IsOrganizationAdmin(permissions.BasePermission):
    """User must be operator_admin for the view's ``organization`` query param
    or the object's organization."""

    message = "Organization administrator privileges required."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        org_id = request.query_params.get("organization") or getattr(
            view, "organization_id", None
        )
        if not org_id:
            return False
        admin_roles = (Role.OPERATOR_ADMIN, Role.PLATFORM_ADMIN)
        return user.memberships.filter(
            is_active=True, organization_id=org_id, role__in=admin_roles
        ).exists()