from django.contrib import admin

from apps.common.models import OutboxEvent


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "state", "aggregate_type", "created_at")
    list_filter = ("state", "event_type")