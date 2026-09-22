from django.contrib import admin

from apps.catalog.models import Place, Quote, RateCard, RateLine, Service


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "service_type", "is_active")
    list_filter = ("service_type", "is_active")


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("name", "place_type", "organization", "is_active")
    list_filter = ("place_type", "is_active")


@admin.register(RateCard)
class RateCardAdmin(admin.ModelAdmin):
    list_display = ("name", "service", "organization", "version", "effective_from", "is_active")
    list_filter = ("is_active", "service")


admin.site.register(RateLine)


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("service", "status", "currency", "total_minor", "created_at")
    list_filter = ("status", "service")