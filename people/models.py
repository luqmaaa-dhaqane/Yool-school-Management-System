from datetime import date

from django.core.exceptions import ValidationError
from django.db import models, transaction

from core.exceptions import DomainError
from core.models import BaseModel, SoftDeleteModel


class Person(BaseModel):
    """Abstract base for anyone with a name (Student, Guardian)."""

    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80)
    date_of_birth = models.DateField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)

    def age(self, on=None):
        if not self.date_of_birth:
            return None
        on = on or date.today()
        dob = self.date_of_birth
        return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


class Student(Person, SoftDeleteModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        GRADUATED = "graduated", "Graduated"
        TRANSFERRED = "transferred", "Transferred"
        WITHDRAWN = "withdrawn", "Withdrawn"
        SUSPENDED = "suspended", "Suspended"

    # State machine: which status may follow which.
    TRANSITIONS = {
        Status.ACTIVE: {Status.GRADUATED, Status.TRANSFERRED, Status.WITHDRAWN, Status.SUSPENDED},
        Status.SUSPENDED: {Status.ACTIVE, Status.WITHDRAWN},
        Status.WITHDRAWN: {Status.ACTIVE},  # re-admission
        Status.GRADUATED: set(),
        Status.TRANSFERRED: set(),
    }

    school = models.ForeignKey("core.School", on_delete=models.PROTECT, related_name="students")
    student_no = models.CharField(
        max_length=20, blank=True, editable=False, help_text="Generated as YYYY-NNNN."
    )
    date_of_birth = models.DateField()  # required for students
    gender = models.CharField(max_length=6, choices=[("male", "Male"), ("female", "Female")])
    place_of_birth = models.CharField(max_length=120, blank=True)
    nationality = models.CharField(max_length=80, blank=True)
    address = models.TextField(blank=True)
    photo = models.ImageField(upload_to="students/", blank=True)
    previous_school = models.CharField(max_length=200, blank=True)
    medical_notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    admitted_on = models.DateField(null=True, blank=True)
    guardians = models.ManyToManyField("Guardian", through="StudentGuardian", related_name="students")

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "student_no"], name="uniq_student_no_per_school"),
        ]
        indexes = [models.Index(fields=["last_name", "first_name"])]
        permissions = [("view_medical_notes", "Can view medical notes")]

    def __str__(self):
        return f"{self.student_no} {self.full_name}".strip()

    # --- validation ------------------------------------------------------
    def clean(self):
        if self.date_of_birth and self.date_of_birth > date.today():
            raise ValidationError({"date_of_birth": "Date of birth cannot be in the future."})

    def save(self, *args, **kwargs):
        if not self.student_no:
            from .services import StudentNumberGenerator

            self.student_no = StudentNumberGenerator.next_number(self.school_id, self.admitted_on)
        super().save(*args, **kwargs)

    # --- status behaviour (state machine) --------------------------------
    def change_status(self, new_status):
        new_status = self.Status(new_status)
        current = self.Status(self.status)
        if new_status == current:
            return
        if new_status not in self.TRANSITIONS[current]:
            raise DomainError(
                f"A student cannot change from {current.label} to {new_status.label}."
            )
        self.status = new_status
        self.save(update_fields=["status", "updated_at"])

    def withdraw(self):
        self.change_status(self.Status.WITHDRAWN)

    def graduate(self):
        self.change_status(self.Status.GRADUATED)

    def transfer_out(self):
        self.change_status(self.Status.TRANSFERRED)

    def suspend(self):
        self.change_status(self.Status.SUSPENDED)

    def reinstate(self):
        self.change_status(self.Status.ACTIVE)

    @property
    def is_active_student(self):
        return self.status == self.Status.ACTIVE

    # --- enrollment ------------------------------------------------------
    def current_enrollment(self):
        return (
            self.enrollments.filter(status="enrolled")
            .select_related("class_section__grade_level", "class_section__academic_year")
            .order_by("-academic_year__starts_on")
            .first()
        )

    def current_class(self):
        enrollment = self.current_enrollment()
        return enrollment.class_section if enrollment else None

    # --- guardians -------------------------------------------------------
    def add_guardian(self, guardian, relationship, is_primary=False, is_emergency_contact=False):
        link, _ = StudentGuardian.objects.update_or_create(
            student=self,
            guardian=guardian,
            defaults={
                "relationship": relationship,
                "is_primary": is_primary,
                "is_emergency_contact": is_emergency_contact,
            },
        )
        return link

    def primary_guardian(self):
        link = self.student_guardians.filter(is_primary=True).select_related("guardian").first()
        return link.guardian if link else None

    def emergency_contacts(self):
        links = self.student_guardians.filter(is_emergency_contact=True).select_related("guardian")
        return [link.guardian for link in links]

    def guardians_to_notify(self):
        """Primary guardian plus emergency contacts, without duplicates."""
        result, seen = [], set()
        primary = self.primary_guardian()
        for g in ([primary] if primary else []) + self.emergency_contacts():
            if g.pk not in seen:
                seen.add(g.pk)
                result.append(g)
        return result


class Guardian(Person):
    school = models.ForeignKey("core.School", on_delete=models.PROTECT, related_name="guardians")
    # Contact record only: guardians do not log in.
    last_name = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=40)
    email = models.EmailField(blank=True)
    occupation = models.CharField(max_length=120, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return self.full_name


class StudentGuardian(BaseModel):
    """Link between a student and a guardian. Exactly one primary per student."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="student_guardians")
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, related_name="student_links")
    relationship = models.CharField(max_length=40)
    is_primary = models.BooleanField(default=False)
    is_emergency_contact = models.BooleanField(default=False)
    can_pickup = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "guardian"], name="uniq_student_guardian"),
            models.UniqueConstraint(
                fields=["student"], condition=models.Q(is_primary=True), name="one_primary_guardian_per_student"
            ),
        ]

    def __str__(self):
        return f"{self.guardian} ({self.relationship}) of {self.student}"

    def save(self, *args, **kwargs):
        if self.student.school_id != self.guardian.school_id:
            raise DomainError("Student and guardian must belong to the same school.")
        with transaction.atomic():
            others = StudentGuardian.objects.filter(student=self.student, is_primary=True).exclude(pk=self.pk)
            if self.is_primary:
                others.update(is_primary=False)
            elif not others.exists():
                self.is_primary = True  # a student's first/only guardian is primary
            super().save(*args, **kwargs)
