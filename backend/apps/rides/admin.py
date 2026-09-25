from django.contrib import admin

from apps.rides.models import (
    DriverOffer,
    DriverShift,
    RideCancellation,
    RideRequest,
    ServiceZone,
    Trip,
    TripEvent,
    TripIncident,
)


@admin.register(ServiceZone)
class ServiceZoneAdmin(admin.ModelAdmin):
    list_display = ["name", "center", "radius_km", "is_active"]


@admin.register(DriverShift)
class DriverShiftAdmin(admin.ModelAdmin):
    list_display = ["driver", "zone", "starts_at", "ends_at", "status"]
    list_filter = ["status"]


@admin.register(RideRequest)
class RideRequestAdmin(admin.ModelAdmin):
    list_display = ["pk", "customer", "pickup", "destination", "status", "created_at"]
    list_filter = ["status"]


@admin.register(DriverOffer)
class DriverOfferAdmin(admin.ModelAdmin):
    list_display = ["ride_request", "driver", "state", "expires_at"]
    list_filter = ["state"]


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ["pk", "ride_request", "driver", "state", "started_at", "ended_at"]
    list_filter = ["state"]


@admin.register(TripEvent)
class TripEventAdmin(admin.ModelAdmin):
    list_display = ["ride_request", "kind", "created_at"]
    list_filter = ["kind"]


@admin.register(RideCancellation)
class RideCancellationAdmin(admin.ModelAdmin):
    list_display = ["ride_request", "reason", "fee_minor", "is_no_show"]


@admin.register(TripIncident)
class TripIncidentAdmin(admin.ModelAdmin):
    list_display = ["trip", "kind", "status", "created_at"]
