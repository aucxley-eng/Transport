from django.db import migrations


def backfill_branches(apps, schema_editor):
    Organization = apps.get_model("accounts", "Organization")
    Branch = apps.get_model("accounts", "Branch")
    Membership = apps.get_model("accounts", "Membership")
    Vehicle = apps.get_model("fleet", "Vehicle")
    Driver = apps.get_model("fleet", "Driver")
    Route = apps.get_model("booking", "Route")
    Departure = apps.get_model("booking", "Departure")
    Ticket = apps.get_model("booking", "Ticket")
    Order = apps.get_model("orders", "Order")
    PaymentAttempt = apps.get_model("payments", "PaymentAttempt")

    for organization in Organization.objects.all():
        branch = Branch.objects.filter(
            organization=organization, is_head_office=True
        ).first()
        if branch is None:
            branch = Branch.objects.create(
                organization=organization,
                name=f"{organization.name} Head Office",
                code="HQ",
                address=organization.address,
                contact_phone=organization.contact_phone,
                is_head_office=True,
                is_active=True,
            )

        Membership.objects.filter(
            organization=organization, branch__isnull=True
        ).exclude(role="platform_admin").update(branch=branch)
        Vehicle.objects.filter(organization=organization, branch__isnull=True).update(
            branch=branch
        )
        Driver.objects.filter(organization=organization, branch__isnull=True).update(
            branch=branch
        )
        Route.objects.filter(organization=organization, branch__isnull=True).update(
            branch=branch
        )
        Departure.objects.filter(
            organization=organization, branch__isnull=True
        ).update(branch=branch)
        Ticket.objects.filter(
            departure__organization=organization, selling_branch__isnull=True
        ).update(selling_branch=branch)
        Order.objects.filter(organization=organization, branch__isnull=True).update(
            branch=branch
        )
        PaymentAttempt.objects.filter(
            organization=organization, branch__isnull=True
        ).update(branch=branch)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_branch_remove_membership_uq_membership_user_org_role_and_more"),
        ("fleet", "0004_driver_branch_vehicle_branch"),
        ("booking", "0002_departure_branch_route_branch_ticket_selling_branch"),
        ("orders", "0003_order_branch"),
        ("payments", "0004_paymentattempt_branch"),
    ]

    operations = [
        migrations.RunPython(backfill_branches, migrations.RunPython.noop),
    ]
