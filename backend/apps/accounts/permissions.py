from rest_framework import permissions

from apps.accounts.models import Role, organization_ids_for


class IsAuthenticated(permissions.IsAuthenticated):
    pass


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
    """Write access for operator/platform administrators.

    Create requests must name an organization the caller administers (in the
    request body, a query param, or ``view.organization_id``). Update/destroy
    and custom detail actions are gated by the record's own organization via
    ``has_object_permission`` plus the org-scoped queryset.
    """

    _ADMIN_ROLES = (Role.OPERATOR_ADMIN, Role.PLATFORM_ADMIN)
    message = "Organization administrator privileges required."

    @staticmethod
    def _admin_org_ids(user):
        """Administered organization ids (strings), or None for superuser
        (grant-all)."""
        if user.is_superuser:
            return None
        return {
            str(org_id)
            for org_id in user.memberships.filter(
                is_active=True, role__in=IsOrganizationAdmin._ADMIN_ROLES
            ).values_list("organization_id", flat=True)
        }

    @staticmethod
    def _org_targets(request, view):
        """Every organization id referenced by the request."""
        targets = set()
        view_org = getattr(view, "organization_id", None)
        if view_org:
            targets.add(str(view_org))
        qp = request.query_params.get("organization")
        if qp:
            targets.add(qp)
        if isinstance(getattr(request, "data", None), dict):
            org = request.data.get("organization")
            if org:
                targets.add(str(org))
        return targets

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        admin = self._admin_org_ids(user)
        if admin is None:
            return True
        if not admin:
            return False
        # Creation targets a fresh organization: every referenced org must be
        # one the caller administers. Detail actions (no org target) are gated
        # on the record itself via has_object_permission.
        if request.method == "POST":
            targets = self._org_targets(request, view)
            if not targets:
                return True
            return targets <= admin
        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        org = getattr(obj, "organization", None)
        if org is None:
            return True
        org_id = org.pk if hasattr(org, "pk") else org
        admin = self._admin_org_ids(user)
        if admin is None:
            return True
        return str(org_id) in admin