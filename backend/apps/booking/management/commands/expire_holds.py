from django.core.management.base import BaseCommand

from apps.booking.services import expire_stale_holds


class Command(BaseCommand):
    help = "Expire timed passenger seat holds that have passed their expiry (blueprint 7)."

    def handle(self, *args, **options):
        expired = expire_stale_holds()
        self.stdout.write(self.style.SUCCESS(f"Expired {expired} seat hold(s)."))
