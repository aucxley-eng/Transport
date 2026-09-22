from django.core.management.base import BaseCommand

from apps.common.models import OutboxEvent
from apps.common.outbox import publish_single


class Command(BaseCommand):
    help = "Publish pending transactional outbox events (run on a schedule or via a worker)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--loop", action="store_true", help="Keep publishing until no pending events remain.")

    def handle(self, *args, **options):
        limit = options["limit"]
        total = 0
        while True:
            pending = (
                OutboxEvent.objects.filter(state="pending")
                .order_by("created_at")
                .values_list("pk", flat=True)[:limit]
            )
            if not pending:
                break
            for pk in pending:
                if publish_single(pk):
                    total += 1
                else:
                    # A failed event is no longer pending; continue past it.
                    continue
            if not options["loop"]:
                break
        self.stdout.write(self.style.SUCCESS(f"Published {total} outbox events."))