from django.contrib import admin

from apps.accounts.models import Membership, Organization, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("phone", "full_name", "email", "is_active", "is_staff")
    search_fields = ("phone", "full_name", "email")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "org_type", "approval_status", "is_active")
    list_filter = ("org_type", "approval_status", "is_active")
    search_fields = ("name", "slug")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("user__phone", "organization__name")