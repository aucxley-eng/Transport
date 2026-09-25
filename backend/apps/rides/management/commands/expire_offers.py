from django.core.management.base import BaseCommand

from apps.rides.services import expire_offers


class Command(BaseCommand):
    help = "Expire pending driver offers past their time limit (blueprint 8)."

    def handle(self, *args, **options):
        expired = expire_offers()
        self.stdout.write(self.style.SUCCESS(f"Expired {expired} driver offer(s)."))
