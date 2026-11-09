from django.core.management.base import BaseCommand

from accounts.permissions import sync_role_groups


class Command(BaseCommand):
    help = "Re-create role groups and permissions. Run after every update; safe to repeat."

    def handle(self, *args, **options):
        sync_role_groups()
        self.stdout.write(self.style.SUCCESS("Roles and permissions synced."))
