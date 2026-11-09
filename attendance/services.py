"""Read-side attendance logic: summaries and report rows."""
from dataclasses import dataclass
from datetime import date

from django.db.models import Count, Q

from .models import AttendanceRecord, AttendanceRegister, Status


@dataclass(frozen=True)
class Summary:
    present: int = 0
    absent: int = 0
    late: int = 0
    excused: int = 0

    @property
    def days(self):
        return self.present + self.absent + self.late + self.excused

    @property
    def rate(self):
        """Attendance rate in percent: (present + late) / recorded days. None if nothing recorded."""
        if not self.days:
            return None
        return round((self.present + self.late) * 100 / self.days, 1)


def _aggregate(records, group_field):
    rows = records.values(group_field).annotate(
        present=Count("id", filter=Q(status=Status.PRESENT)),
        absent=Count("id", filter=Q(status=Status.ABSENT)),
        late=Count("id", filter=Q(status=Status.LATE)),
        excused=Count("id", filter=Q(status=Status.EXCUSED)),
    )
    return {
        row[group_field]: Summary(row["present"], row["absent"], row["late"], row["excused"]) for row in rows
    }


def _in_range(records, start, end):
    if start:
        records = records.filter(register__date__gte=start)
    if end:
        records = records.filter(register__date__lte=end)
    return records


def enrollment_summary(enrollment, start=None, end=None):
    records = _in_range(AttendanceRecord.objects.filter(enrollment=enrollment), start, end)
    return _aggregate(records, "enrollment_id").get(enrollment.pk, Summary())


def student_rows(section, start=None, end=None):
    """One row per student of the class with their summary for the period."""
    enrollments = list(
        section.enrollments.select_related("student").order_by("student__last_name", "student__first_name")
    )
    records = _in_range(AttendanceRecord.objects.filter(enrollment__in=enrollments), start, end)
    summaries = _aggregate(records, "enrollment_id")
    return [{"enrollment": e, "student": e.student, "summary": summaries.get(e.pk, Summary())} for e in enrollments]


def class_day_rows(sections, day):
    """For each class: how many students, whether the register was taken, and the day's counts."""
    sections = list(sections)
    registers = {
        r.class_section_id: r
        for r in AttendanceRegister.objects.filter(date=day, class_section__in=sections).select_related("taken_by")
    }
    summaries = _aggregate(
        AttendanceRecord.objects.filter(register__in=registers.values()), "register_id"
    )
    rows = []
    for section in sections:
        register = registers.get(section.pk)
        rows.append({
            "section": section,
            "students": len(section.enrollments_on(day)),
            "register": register,
            "summary": summaries.get(register.pk, Summary()) if register else None,
            "problem": AttendanceRegister.day_problem(section, day),
        })
    return rows


def chronic_absentees(sections, start, end, threshold=90.0, min_days=5):
    """Students whose attendance rate is below ``threshold`` percent (needs at least ``min_days`` recorded)."""
    enrollments = list(
        _enrollments_for(sections).select_related("student", "class_section__grade_level")
    )
    records = _in_range(AttendanceRecord.objects.filter(enrollment__in=enrollments), start, end)
    summaries = _aggregate(records, "enrollment_id")
    result = []
    for e in enrollments:
        summary = summaries.get(e.pk)
        if summary and summary.days >= min_days and summary.rate < threshold:
            guardian = e.student.primary_guardian()
            result.append({
                "enrollment": e, "student": e.student, "section": e.class_section, "summary": summary,
                "guardian": guardian,
            })
    result.sort(key=lambda r: r["summary"].rate)
    return result


def _enrollments_for(sections):
    from academics.models import Enrollment

    return Enrollment.objects.filter(class_section__in=sections)
