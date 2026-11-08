from datetime import date

from django.db import transaction


class StudentNumberGenerator:
    """Generates IDs like 2026-0001 (year of admission + sequence per school)."""

    @staticmethod
    @transaction.atomic
    def next_number(school_id, admitted_on=None):
        from .models import Student

        year = (admitted_on or date.today()).year
        prefix = f"{year}-"
        last = (
            Student.all_objects.filter(school_id=school_id, student_no__startswith=prefix)
            .order_by("-student_no")
            .first()
        )
        seq = int(last.student_no.split("-")[1]) + 1 if last else 1
        return f"{year}-{seq:04d}"
