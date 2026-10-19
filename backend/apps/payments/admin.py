from django.contrib import admin

from apps.payments.models import LedgerEntry, PaymentAttempt, Refund


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ("provider", "state", "expected_amount_minor", "currency", "created_at")
    list_filter = ("provider", "state", "currency")
    search_fields = ("provider_reference",)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "journal", "account", "entry_type", "amount_minor", "currency")
    list_filter = ("journal", "entry_type")
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("reference", "order", "amount_minor", "currency", "created_at")
    search_fields = ("reference",)
    readonly_fields = [f.name for f in Refund._meta.fields]
