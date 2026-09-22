"""Seed non-production development data (blueprint 22): a platform org,
superuser, base catalog and a sample operator with vehicle, driver and rates."""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Membership, Organization, Role
from apps.catalog.models import Place, RateCard, RateLine, Service
from apps.fleet.models import Driver, Vehicle

User = get_user_model()


class Command(BaseCommand):
    help = "Seed development-only data. Never run against production."

    @transaction.atomic
    def handle(self, *args, **options):
        platform, _ = Organization.objects.get_or_create(
            slug="platform",
            defaults={
                "name": "Kenya Transport Platform",
                "org_type": Organization.Type.PLATFORM,
                "approval_status": Organization.Approval.APPROVED,
            },
        )

        operator, _ = Organization.objects.get_or_create(
            slug="demo-sacco",
            defaults={
                "name": "Demo Sacco Ltd",
                "org_type": Organization.Type.CARRIER,
                "approval_status": Organization.Approval.APPROVED,
                "contact_phone": "+254722000000",
            },
        )

        admin, created = User.objects.get_or_create(
            phone="+254700000001",
            defaults={"full_name": "Platform Admin", "email": "admin@transport.test", "is_staff": True},
        )
        if created:
            admin.is_superuser = True
            admin.set_password("admin-password-1")
            admin.save()
        Membership.objects.get_or_create(
            user=admin, organization=platform, role=Role.PLATFORM_ADMIN
        )
        Membership.objects.get_or_create(
            user=admin, organization=operator, role=Role.OPERATOR_ADMIN
        )

        operator_user, created = User.objects.get_or_create(
            phone="+254700000002",
            defaults={"full_name": "Demo Operator"},
        )
        if created:
            operator_user.set_password("operator-password-1")
            operator_user.save()
        Membership.objects.get_or_create(
            user=operator_user, organization=operator, role=Role.OPERATOR_ADMIN
        )

        services = {}
        for slug, name, stype in [
            ("scheduled-bus", "Scheduled Bus", Service.ServiceType.SCHEDULED),
            ("on-demand-ride", "On-demand Ride", Service.ServiceType.RIDE),
            ("local-courier", "Local Courier", Service.ServiceType.COURIER),
            ("intercity-parcel", "Intercity Parcel", Service.ServiceType.PARCEL),
            ("freight", "Freight", Service.ServiceType.FREIGHT),
            ("warehouse", "Warehousing", Service.ServiceType.WAREHOUSE),
        ]:
            service, _ = Service.objects.get_or_create(
                slug=slug,
                defaults={"name": name, "service_type": stype},
            )
            services[stype] = service

        for name, ptype in [
            ("Nairobi CBD", Place.PlaceType.STAGE),
            ("Nakuru Junction", Place.PlaceType.STAGE),
            ("Eldoret Stage", Place.PlaceType.STAGE),
            ("Nairobi Hub", Place.PlaceType.HUB),
            ("Mombasa Branch", Place.PlaceType.BRANCH),
        ]:
            Place.objects.get_or_create(
                name=name,
                defaults={"place_type": ptype, "organization": operator},
            )

        card, card_created = RateCard.objects.get_or_create(
            organization=operator,
            service=services[Service.ServiceType.COURIER],
            version=1,
            defaults={
                "name": "Courier Zone A",
                "currency": "KES",
                "effective_from": date.today(),
                "is_active": True,
            },
        )
        if card_created:
            RateLine.objects.create(rate_card=card, line_type=RateLine.LineType.FIXED,
                                    label="Base", amount_minor=20000)  # KES 200.00
            RateLine.objects.create(rate_card=card, line_type=RateLine.LineType.PER_KG,
                                    label="Per kg", amount_minor=5000)  # KES 50.00

        vehicle, created = Vehicle.objects.get_or_create(
            registration_number="KDX 123Z",
            defaults={
                "organization": operator,
                "vehicle_class": Vehicle.VehicleClass.SHUTTLE,
                "make": "Toyota",
                "model": "Hiace",
                "year": 2021,
                "seating_capacity": 14,
                "baggage_capacity_kg": 200,
                "cargo_weight_kg": 400,
            },
        )

        Driver.objects.get_or_create(
            user=operator_user,
            defaults={
                "organization": operator,
                "license_number": "LIC-0001",
                "status": Driver.Status.APPROVED,
            },
        )

        self.stdout.write(self.style.SUCCESS(
            "Seeded: platform, demo-sacco, superuser (+254700000001), "
            "services, rate card, vehicle KDX 123Z."
        ))