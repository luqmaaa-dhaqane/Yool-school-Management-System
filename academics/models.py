from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from core.exceptions import DomainError
from core.models import BaseModel


class AcademicYear(BaseModel):
    school = models.ForeignKey("core.School", on_delete=models.PROTECT, related_name="academic_years")
    name = models.CharField(max_length=40)
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_current = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)

    class Meta:
        ordering = ["-starts_on"]
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="uniq_year_name_per_school"),
            models.UniqueConstraint(
                fields=["school"], condition=models.Q(is_current=True), name="one_current_year_per_school"
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.starts_on and self.ends_on and self.ends_on <= self.starts_on:
            raise ValidationError({"ends_on": "End date must be after the start date."})

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_current:
                AcademicYear.objects.filter(school=self.school, is_current=True).exclude(pk=self.pk).update(
                    is_current=False
                )
            super().save(*args, **kwargs)

    def make_current(self):
        self.is_current = True
        self.save()

    def lock(self):
        """Close the year: it and all its terms become read-only for grades/attendance."""
        with transaction.atomic():
            self.terms.update(is_locked=True)
            self.is_locked = True
            self.save(update_fields=["is_locked", "updated_at"])

    def unlock(self):
        self.is_locked = False
        self.save(update_fields=["is_locked", "updated_at"])

    def contains(self, day):
        return self.starts_on <= day <= self.ends_on

    def term_on(self, day):
        """The term running on ``day`` or None (holidays between terms)."""
        return self.terms.filter(starts_on__lte=day, ends_on__gte=day).first()


class Term(BaseModel):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="terms")
    name = models.CharField(max_length=40)
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_locked = models.BooleanField(default=False)

    class Meta:
        ordering = ["starts_on"]
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "name"], name="uniq_term_name_per_year"),
        ]

    def __str__(self):
        return f"{self.academic_year} - {self.name}"

    def clean(self):
        if self.starts_on and self.ends_on:
            if self.ends_on <= self.starts_on:
                raise ValidationError({"ends_on": "End date must be after the start date."})
            year = self.academic_year if self.academic_year_id else None
            if year and not (year.contains(self.starts_on) and year.contains(self.ends_on)):
                raise ValidationError("Term dates must fall inside the academic year.")

    def contains(self, day):
        return self.starts_on <= day <= self.ends_on

    def is_open(self):
        """Open for grade and attendance changes."""
        return not self.is_locked and not self.academic_year.is_locked

    def lock(self):
        self.is_locked = True
        self.save(update_fields=["is_locked", "updated_at"])

    def unlock(self):
        self.is_locked = False
        self.save(update_fields=["is_locked", "updated_at"])


class GradeLevel(BaseModel):
    school = models.ForeignKey("core.School", on_delete=models.PROTECT, related_name="grade_levels")
    name = models.CharField(max_length=40)
    sort_order = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["sort_order"]
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="uniq_grade_name_per_school"),
        ]

    def __str__(self):
        return self.name


class ClassSection(BaseModel):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="sections")
    grade_level = models.ForeignKey(GradeLevel, on_delete=models.PROTECT, related_name="sections")
    name = models.CharField(max_length=20, help_text="For example A, B, Blue.")
    homeroom_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        limit_choices_to={"role": "teacher"},
        related_name="homeroom_sections",
    )
    capacity = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["academic_year", "grade_level__sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "grade_level", "name"], name="uniq_section_per_year_grade"
            ),
        ]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return f"{self.grade_level.name} {self.name}"

    # --- roster and capacity ---------------------------------------------
    def active_enrollments(self):
        return self.enrollments.filter(status=Enrollment.Status.ENROLLED)

    def enrolled_count(self):
        return self.active_enrollments().count()

    def seats_left(self):
        if self.capacity is None:
            return None
        return max(self.capacity - self.enrolled_count(), 0)

    def has_capacity(self):
        return self.capacity is None or self.enrolled_count() < self.capacity

    def enrollments_between(self, start, end):
        """Students who belonged to this class at any time between two dates (for a term's grades)."""
        return list(
            self.enrollments.filter(enrolled_on__lte=end)
            .filter(models.Q(left_on__isnull=True) | models.Q(left_on__gte=start))
            .select_related("student")
            .order_by("student__last_name", "student__first_name")
        )

    def add_all_subjects(self):
        """Create a (teacher-less) ClassSubject for every school subject this class does not have yet."""
        existing = set(self.class_subjects.values_list("subject_id", flat=True))
        missing = Subject.objects.filter(school=self.academic_year.school).exclude(pk__in=existing)
        ClassSubject.objects.bulk_create([ClassSubject(class_section=self, subject=sub) for sub in missing])
        return missing.count()

    def enrollments_on(self, day):
        """Students who belonged to this class on ``day`` (joined by then, not yet left)."""
        return list(
            self.enrollments.filter(enrolled_on__lte=day)
            .filter(models.Q(left_on__isnull=True) | models.Q(left_on__gte=day))
            .select_related("student")
            .order_by("student__last_name", "student__first_name")
        )

    def roster(self):
        """Active enrollments, alphabetical by student name."""
        return list(
            self.active_enrollments()
            .select_related("student")
            .order_by("student__last_name", "student__first_name")
        )


class Subject(BaseModel):
    school = models.ForeignKey("core.School", on_delete=models.PROTECT, related_name="subjects")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    name_so = models.CharField("Name (Somali)", max_length=100, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "code"], name="uniq_subject_code_per_school"),
        ]

    def __str__(self):
        return self.name


class ClassSubject(BaseModel):
    """A subject taught in a class, and who teaches it (the 'teaching assignment')."""

    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="class_subjects")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="class_subjects")
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        limit_choices_to={"role": "teacher"},
        related_name="teaching_assignments",
    )

    class Meta:
        ordering = ["class_section", "subject__name"]
        constraints = [
            models.UniqueConstraint(fields=["class_section", "subject"], name="uniq_subject_per_class"),
        ]

    def __str__(self):
        return f"{self.class_section} - {self.subject}"

    def clean(self):
        if self.subject_id and self.class_section_id:
            if self.subject.school_id != self.class_section.academic_year.school_id:
                raise ValidationError("The subject belongs to a different school.")

    def can_be_graded_by(self, user):
        """Who may enter or change grades: the subject's teacher, the Principal, or a super admin."""
        if user.is_superuser:
            return True
        if user.school_id != self.class_section.academic_year.school_id:
            return False
        if user.role == "principal":
            return True
        return user.role == "teacher" and self.teacher_id == user.pk


class Enrollment(BaseModel):
    """A student's place in a class section for one academic year.

    All changes of state go through methods (withdraw, promote, ...) so the
    rules cannot be bypassed.
    """

    class Status(models.TextChoices):
        ENROLLED = "enrolled", "Enrolled"
        PROMOTED = "promoted", "Promoted"
        REPEATED = "repeated", "Repeated the year"
        GRADUATED = "graduated", "Graduated"
        TRANSFERRED = "transferred", "Transferred out"
        WITHDRAWN = "withdrawn", "Withdrawn"

    student = models.ForeignKey("people.Student", on_delete=models.PROTECT, related_name="enrollments")
    class_section = models.ForeignKey(ClassSection, on_delete=models.PROTECT, related_name="enrollments")
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="enrollments", editable=False
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ENROLLED)
    enrolled_on = models.DateField(default=date.today)
    left_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-academic_year__starts_on", "student__last_name", "student__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year"],
                condition=models.Q(status="enrolled"),
                name="one_active_enrollment_per_student_year",
            ),
        ]
        indexes = [models.Index(fields=["class_section", "status"])]

    def __str__(self):
        return f"{self.student} in {self.class_section} ({self.academic_year})"

    # --- rules -----------------------------------------------------------
    def _check_rules(self):
        """Raise DomainError if this enrollment may not exist as it is now."""
        if self.status != self.Status.ENROLLED:
            return
        section = self.class_section
        year = section.academic_year
        if year.is_locked:
            raise DomainError(f"The academic year {year} is locked.")
        if self.student.school_id != year.school_id:
            raise DomainError("The student and the class belong to different schools.")
        if self._state.adding and not self.student.is_active_student:
            raise DomainError(
                f"{self.student.full_name} is {self.student.get_status_display().lower()} "
                "and cannot be enrolled."
            )
        clash = Enrollment.objects.filter(
            student=self.student, academic_year=year, status=self.Status.ENROLLED
        ).exclude(pk=self.pk)
        if clash.exists():
            other = clash.first().class_section
            raise DomainError(f"{self.student.full_name} is already enrolled in {other} for {year}.")
        if section.capacity is not None:
            taken = section.active_enrollments().exclude(pk=self.pk).count()
            if taken >= section.capacity:
                raise DomainError(f"{section} is full ({section.capacity} students).")

    def clean(self):
        if not (self.class_section_id and self.student_id):
            return
        self.academic_year_id = self.class_section.academic_year_id
        if not self._state.adding:
            old_year = (
                Enrollment.objects.filter(pk=self.pk).values_list("academic_year_id", flat=True).first()
            )
            if old_year and old_year != self.academic_year_id:
                raise ValidationError("A class change must stay inside the same academic year.")
        try:
            self._check_rules()
        except DomainError as exc:
            raise ValidationError(str(exc))

    def save(self, *args, **kwargs):
        self.academic_year_id = self.class_section.academic_year_id
        if self._state.adding:
            self._check_rules()
        super().save(*args, **kwargs)

    # --- queries ---------------------------------------------------------
    def is_active(self):
        return self.status == self.Status.ENROLLED

    # --- creating --------------------------------------------------------
    @classmethod
    def enroll(cls, student, section, on=None):
        with transaction.atomic():
            ClassSection.objects.select_for_update().get(pk=section.pk)  # serialise seat checks
            return cls.objects.create(student=student, class_section=section, enrolled_on=on or date.today())

    # --- closing ---------------------------------------------------------
    def _close(self, status, on=None, reason=""):
        if not self.is_active():
            raise DomainError(f"This enrollment is already closed ({self.get_status_display()}).")
        self.status = status
        self.left_on = on or date.today()
        if reason:
            self.notes = f"{self.notes}\n{reason}".strip()
        self.save(update_fields=["status", "left_on", "notes", "updated_at"])

    def withdraw(self, on=None, reason=""):
        with transaction.atomic():
            self.student.withdraw()
            self._close(self.Status.WITHDRAWN, on, reason)

    def transfer_out(self, on=None, reason=""):
        with transaction.atomic():
            self.student.transfer_out()
            self._close(self.Status.TRANSFERRED, on, reason)

    def graduate(self, on=None):
        with transaction.atomic():
            self.student.graduate()
            self._close(self.Status.GRADUATED, on)

    # --- moving ahead ----------------------------------------------------
    def _check_next(self, next_section, *, higher_grade):
        if next_section.academic_year.school_id != self.academic_year.school_id:
            raise DomainError("The target class belongs to a different school.")
        if next_section.academic_year.starts_on <= self.academic_year.starts_on:
            raise DomainError("The target class must be in a later academic year.")
        current = self.class_section.grade_level.sort_order
        target = next_section.grade_level.sort_order
        if higher_grade and target <= current:
            raise DomainError("A promoted student must move to a higher grade.")
        if not higher_grade and target != current:
            raise DomainError("A student repeating the year must stay in the same grade.")

    def promote(self, next_section, on=None):
        """Close this enrollment as PROMOTED and enroll the student in a higher grade next year."""
        if not self.is_active():
            raise DomainError(f"This enrollment is already closed ({self.get_status_display()}).")
        self._check_next(next_section, higher_grade=True)
        with transaction.atomic():
            self._close(self.Status.PROMOTED, on)
            return Enrollment.enroll(self.student, next_section, on)

    def repeat(self, next_section, on=None):
        """Close this enrollment as REPEATED and enroll the student in the same grade next year."""
        if not self.is_active():
            raise DomainError(f"This enrollment is already closed ({self.get_status_display()}).")
        self._check_next(next_section, higher_grade=False)
        with transaction.atomic():
            self._close(self.Status.REPEATED, on)
            return Enrollment.enroll(self.student, next_section, on)
