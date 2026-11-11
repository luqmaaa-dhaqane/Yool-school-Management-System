from django.core.management.base import BaseCommand

from accounts.permissions import sync_role_groups
from core.models import School
from grades.setup import ensure_grading_setup


class Command(BaseCommand):
    help = "Run after every update: refresh roles and permissions and add any missing set-up. Safe to repeat."

    def handle(self, *args, **options):
        sync_role_groups()
        self.stdout.write("Roles and permissions synced.")
        for school in School.objects.all():
            added = ensure_grading_setup(school)
            self.stdout.write(f"{school.name}: grading set-up checked ({added} subject(s) added to classes).")
        self.stdout.write(self.style.SUCCESS("Update finished."))
