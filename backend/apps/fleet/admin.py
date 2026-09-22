from django.contrib import admin

from apps.fleet.models import Document, Driver, Vehicle


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("registration_number", "vehicle_class", "status", "organization")
    list_filter = ("vehicle_class", "status")


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "status", "license_number")
    list_filter = ("status",)
    search_fields = ("user__phone", "license_number")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("doc_type", "reference", "entity_type", "status", "expires_on")
    list_filter = ("doc_type", "status")