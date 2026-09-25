from django.contrib import admin

from apps.corporate.models import (
    CorporateMember,
    CorporatePolicy,
    CostCenter,
    ShuttleRosterEntry,
    ShuttleRun,
    ShuttleTemplate,
    TravelRequest,
)


@admin.register(CorporatePolicy)
class CorporatePolicyAdmin(admin.ModelAdmin):
    list_display = ["organization", "approval_threshold_minor", "is_active"]
    filter_horizontal = ["permitted_services"]


@admin.register(CostCenter)
class CostCenterAdmin(admin.ModelAdmin):
    list_display = ["organization", "code", "name", "budget_minor", "is_active"]


@admin.register(CorporateMember)
class CorporateMemberAdmin(admin.ModelAdmin):
    list_display = ["organization", "employee_ref", "user", "cost_center", "is_active", "left_on"]
    list_filter = ["is_active"]


@admin.register(TravelRequest)
class TravelRequestAdmin(admin.ModelAdmin):
    list_display = ["pk", "organization", "member", "cost_center", "estimated_minor", "status"]
    list_filter = ["status", "requires_approval"]


@admin.register(ShuttleTemplate)
class ShuttleTemplateAdmin(admin.ModelAdmin):
    list_display = ["organization", "name", "route", "departure_time", "capacity", "is_active"]


@admin.register(ShuttleRun)
class ShuttleRunAdmin(admin.ModelAdmin):
    list_display = ["template", "run_date", "departure"]


@admin.register(ShuttleRosterEntry)
class ShuttleRosterEntryAdmin(admin.ModelAdmin):
    list_display = ["run", "member", "seat_number", "state"]
    list_filter = ["state"]
