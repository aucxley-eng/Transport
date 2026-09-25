from django.contrib import admin

from apps.charter.models import (
    CharterDeposit,
    CharterExtra,
    CharterReservation,
    HireRequest,
)


@admin.register(HireRequest)
class HireRequestAdmin(admin.ModelAdmin):
    list_display = ["pk", "organization", "customer", "start_at", "end_at", "status"]
    list_filter = ["status"]


@admin.register(CharterReservation)
class CharterReservationAdmin(admin.ModelAdmin):
    list_display = ["hire_request", "vehicle", "driver", "blocked_from", "blocked_to"]


@admin.register(CharterExtra)
class CharterExtraAdmin(admin.ModelAdmin):
    list_display = ["hire_request", "kind", "price_minor", "is_permitted"]


@admin.register(CharterDeposit)
class CharterDepositAdmin(admin.ModelAdmin):
    list_display = ["hire_request", "amount_minor", "payment_attempt", "created_at"]
