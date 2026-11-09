from collections import Counter
from datetime import date

from django.conf import settings
from django.db import models, transaction

from audit.models import AuditLog
from core.exceptions import DomainError
from core.models import BaseModel


class Status(models.TextChoices):
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    LATE = "late", "Late"
    EXCUSED = "excused", "Excused"


class Holiday(BaseModel):
    """A day the school is closed (in addition to the weekly non-teaching days)."""

    school = models.ForeignKey("core.School", on_delete=models.PROTECT, related_name="holidays")
    date = models.DateField()
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["school", "date"], name="uniq_holiday_per_school_day")]

    def __str__(self):
        return f"{self.date} {self.name}"


class AttendanceRegister(BaseModel):
    """One class's register for one day. Created and changed only through ``take``."""

    class_section = models.ForeignKey("academics.ClassSection", on_delete=models.PROTECT, related_name="registers")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="registers")
    date = models.DateField()
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Last person who saved this register.",
    )

    class Meta:
        ordering = ["-date", "class_section"]
        constraints = [models.UniqueConstraint(fields=["class_section", "date"], name="uniq_register_per_class_day")]
        indexes = [models.Index(fields=["date"])]

    def __str__(self):
        return f"{self.class_section} on {self.date}"

    # --- rules -----------------------------------------------------------
    @staticmethod
    def check_day(section, day, actor=None, today=None):
        """Return the Term if attendance may be recorded; otherwise raise DomainError saying why."""
        today = today or date.today()
        if day > today:
            raise DomainError("Attendance cannot be taken for a future date.")
        year = section.academic_year
        term = year.term_on(day)
        if term is None:
            raise DomainError(f"{day:%d %b %Y} is not inside any term of {year}.")
        if day.weekday() in settings.SCHOOL_NON_TEACHING_WEEKDAYS:
            raise DomainError(f"{day:%A %d %b} is a non-teaching day.")
        holiday = Holiday.objects.filter(school=year.school, date=day).first()
        if holiday:
            raise DomainError(f"{day:%d %b %Y} is a holiday ({holiday.name}).")
        if not term.is_open():
            raise DomainError(f"{term.name} is locked. Unlock it to change attendance.")
        if actor is not None:
            if not actor.can_take_attendance_for(section):
                raise DomainError("You are not assigned to this class.")
            if actor.role == "teacher" and (today - day).days > settings.ATTENDANCE_EDIT_DAYS:
                raise DomainError(
                    f"Teachers can only change attendance up to {settings.ATTENDANCE_EDIT_DAYS} days back. "
                    "Ask the Principal or Registrar."
                )
        return term

    @classmethod
    def day_problem(cls, section, day, actor=None, today=None):
        """The reason attendance cannot be taken, or None."""
        try:
            cls.check_day(section, day, actor, today)
        except DomainError as exc:
            return str(exc)
        return None

    # --- behaviour -------------------------------------------------------
    @classmethod
    @transaction.atomic
    def take(cls, section, day, marks, actor, today=None):
        """Record or correct a class's attendance for one day.

        ``marks`` maps enrollment id -> (status, reason). Students not mentioned
        are Present. Saving again updates the existing register (never duplicates).
        """
        term = cls.check_day(section, day, actor, today)
        eligible = {str(e.pk): e for e in section.enrollments_on(day)}
        marks = {str(k): v for k, v in marks.items()}
        if set(marks) - set(eligible):
            raise DomainError("Some students do not belong to this class on that date.")

        register, created = cls.objects.get_or_create(
            class_section=section, date=day, defaults={"term": term, "taken_by": actor}
        )
        existing = {str(r.enrollment_id): r for r in register.records.all()}
        counts, new_records = Counter(), []
        for key, enrollment in eligible.items():
            raw_status, reason = marks.get(key, (Status.PRESENT, ""))
            try:
                status = Status(raw_status)
            except ValueError:
                raise DomainError(f"'{raw_status}' is not a valid attendance status.")
            reason = "" if status == Status.PRESENT else (reason or "").strip()[:200]
            counts[status] += 1
            record = existing.get(key)
            if record is None:
                new_records.append(AttendanceRecord(register=register, enrollment=enrollment, status=status, reason=reason))
            elif (record.status, record.reason) != (status, reason):
                record.status, record.reason = status, reason
                record.save(update_fields=["status", "reason", "updated_at"])
        AttendanceRecord.objects.bulk_create(new_records)
        register.taken_by = actor
        register.save(update_fields=["taken_by", "updated_at"])
        AuditLog.record(
            actor, "attendance_taken" if created else "attendance_edited", register,
            after={s.value: counts.get(s, 0) for s in Status},
        )
        return register

    def counts(self):
        data = Counter(self.records.values_list("status", flat=True))
        return {s.value: data.get(s.value, 0) for s in Status}


class AttendanceRecord(BaseModel):
    register = models.ForeignKey(AttendanceRegister, on_delete=models.CASCADE, related_name="records")
    enrollment = models.ForeignKey("academics.Enrollment", on_delete=models.PROTECT, related_name="attendance")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PRESENT)
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["register__date", "enrollment__student__last_name"]
        constraints = [models.UniqueConstraint(fields=["register", "enrollment"], name="uniq_record_per_student_register")]
        indexes = [models.Index(fields=["enrollment", "status"])]

    def __str__(self):
        return f"{self.enrollment.student} {self.register.date}: {self.get_status_display()}"
