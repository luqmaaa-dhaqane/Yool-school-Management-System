from django.core.management.base import BaseCommand, CommandError

from core.models import School
from people.importers import StudentImporter


class Command(BaseCommand):
    help = "Import students from a CSV file. Dry run unless --commit is given."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--school", help="School name (optional if only one school exists).")
        parser.add_argument("--commit", action="store_true", help="Actually save (default is preview only).")

    def handle(self, *args, **opts):
        schools = School.objects.filter(name=opts["school"]) if opts["school"] else School.objects.all()
        if schools.count() != 1:
            raise CommandError("Could not pick a single school; use --school.")
        with open(opts["path"], "rb") as handle:
            report = StudentImporter(schools.first()).run(handle, dry_run=not opts["commit"])
        for line, message in report.errors:
            self.stdout.write(self.style.ERROR(f"line {line}: {message}"))
        verb = "Imported" if report.committed else "Would import"
        self.stdout.write(f"{verb} {report.created} students ({report.enrolled} placed in classes, "
                          f"{report.guardians_created} new guardian contacts). Errors: {len(report.errors)}.")
        if report.errors:
            raise CommandError("Nothing was saved because of the errors above.")
