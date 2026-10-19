from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payments"

    def ready(self):
        # Register the outbox subscribers that settle revenue. Importing for
        # the side effect of the @subscribe decorators is the wiring.
        from apps.payments import subscribers  # noqa: F401
