from django.contrib import admin

from apps.booking.models import (
    BoardingScan,
    Departure,
    DepartureException,
    Route,
    RouteStop,
    SeatHold,
    Ticket,
)


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "is_active"]
    search_fields = ["name"]


@admin.register(RouteStop)
class RouteStopAdmin(admin.ModelAdmin):
    list_display = ["route", "place", "position"]


@admin.register(Departure)
class DepartureAdmin(admin.ModelAdmin):
    list_display = ["route", "departure_date", "departure_time", "capacity", "status"]
    list_filter = ["status", "departure_date"]


@admin.register(SeatHold)
class SeatHoldAdmin(admin.ModelAdmin):
    list_display = ["departure", "seat_number", "state", "expires_at"]
    list_filter = ["state"]


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ["reference", "departure", "seat_number", "state", "passenger_name"]
    search_fields = ["reference", "token", "passenger_name", "passenger_phone"]


@admin.register(BoardingScan)
class BoardingScanAdmin(admin.ModelAdmin):
    list_display = ["ticket", "departure", "boarded", "scanner_device", "created_at"]


@admin.register(DepartureException)
class DepartureExceptionAdmin(admin.ModelAdmin):
    list_display = ["departure", "kind", "requires_customer_communication", "created_at"]
