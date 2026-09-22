from django.contrib import admin

from apps.audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "entity_type", "actor", "organization")
    list_filter = ("action", "entity_type")
    search_fields = ("entity_id", "reason", "actor__phone")
    readonly_fields = [f.name for f in AuditEvent._meta.fields]