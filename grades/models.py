from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

from core.exceptions import DomainError
from core.models import BaseModel


class AssessmentType(BaseModel):
    """A kind of assessment and how much it counts toward the term grade (e.g. Quiz 20, Midterm 30, Exam 50)."""

    school = models.ForeignKey("core.School", on_delete=models.PROTECT, related_name="assessment_types")
    name = models.CharField(max_length=60)
    weight_percent = models.DecimalField(
        max_digits=5, decimal_places=2, validators=[MinValueValidator(0)],
        help_text="Share of the term grade. The weights of all types should add up to 100.",
    )

    class Meta:
        ordering = ["-weight_percent", "name"]
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="uniq_assessment_type_per_school")]

    def __str__(self):
        return f"{self.name} ({self.weight_percent:g}%)"

    def clean(self):
        if self.weight_percent is not None and self.weight_percent > 100:
            raise ValidationError({"weight_percent": "A weight cannot be more than 100."})


class GradeBand(BaseModel):
    """A letter grade and the lowest percentage that earns it (A from 90, B from 80, ...)."""

    school = models.ForeignKey("core.School", on_delete=models.PROTECT, related_name="grade_bands")
    label = models.CharField(max_length=10)
    min_percent = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0)])
    remark = models.CharField(max_length=60, blank=True, help_text="For example: Excellent.")

    class Meta:
        ordering = ["-min_percent"]
        constraints = [
            models.UniqueConstraint(fields=["school", "label"], name="uniq_band_label_per_school"),
            models.UniqueConstraint(fields=["school", "min_percent"], name="uniq_band_min_per_school"),
        ]

    def __str__(self):
        return f"{self.label} (from {self.min_percent:g}%)"


class Assessment(BaseModel):
    """One quiz, test or exam for a subject in a class during a term."""

    class_subject = models.ForeignKey("academics.ClassSubject", on_delete=models.PROTECT, related_name="assessments")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="assessments")
    assessment_type = models.ForeignKey(AssessmentType, on_delete=models.PROTECT, related_name="assessments")
    title = models.CharField(max_length=100, help_text="For example: Quiz 1, Midterm.")
    max_score = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    held_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["term__starts_on", "held_on", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["class_subject", "term", "title"], name="uniq_assessment_title"),
        ]

    def __str__(self):
        return f"{self.title} ({self.class_subject})"

    # --- rules -----------------------------------------------------------
    def _check_rules(self):
        section = self.class_subject.class_section
        if self.term.academic_year_id != section.academic_year_id:
            raise DomainError("The term belongs to a different academic year than the class.")
        if self.assessment_type.school_id != section.academic_year.school_id:
            raise DomainError("The assessment type belongs to a different school.")
        if not self.term.is_open():
            raise DomainError(f"{self.term.name} is locked. Unlock it to change grades.")
        if self.held_on and not self.term.contains(self.held_on):
            raise DomainError("The assessment date must fall inside the term.")

    def clean(self):
        if not (self.class_subject_id and self.term_id and self.assessment_type_id):
            return
        try:
            self._check_rules()
        except DomainError as exc:
            raise ValidationError(str(exc))

    def save(self, *args, **kwargs):
        self._check_rules()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.term.is_open():
            raise DomainError("This term is locked.")
        return super().delete(*args, **kwargs)

    # --- scores ----------------------------------------------------------
    def eligible_enrollments(self):
        """Students who were in the class at some point during the term."""
        return self.class_subject.class_section.enrollments_between(self.term.starts_on, self.term.ends_on)

    def percent(self, score):
        return (Decimal(score) * 100 / self.max_score) if score is not None else None

    @transaction.atomic
    def record_scores(self, scores, actor):
        """Save or clear scores. ``scores`` maps enrollment id -> number, or None to clear.

        All or nothing: one invalid score rejects the whole batch.
        """
        if not self.class_subject.can_be_graded_by(actor):
            raise DomainError("You are not allowed to enter grades for this subject.")
        if not self.term.is_open():
            raise DomainError(f"{self.term.name} is locked. Unlock it to change grades.")
        eligible = {str(e.pk): e for e in self.eligible_enrollments()}
        scores = {str(k): v for k, v in scores.items()}
        if set(scores) - set(eligible):
            raise DomainError("Some students do not belong to this class during this term.")
        existing = {str(g.enrollment_id): g for g in self.entries.all()}
        set_count = cleared = 0
        for key, raw in scores.items():
            if raw is None or raw == "":
                if key in existing:
                    existing[key].delete()
                    cleared += 1
                continue
            try:
                value = Decimal(str(raw))
            except InvalidOperation:
                raise DomainError(f"'{raw}' is not a valid score.")
            if value < 0 or value > self.max_score:
                name = eligible[key].student.full_name
                raise DomainError(f"{name}: the score for {self.title} must be between 0 and {self.max_score:g}.")
            value = value.quantize(Decimal("0.01"))
            entry = existing.get(key)
            if entry is None:
                GradeEntry.objects.create(assessment=self, enrollment=eligible[key], score=value, entered_by=actor)
                set_count += 1
            elif entry.score != value:
                entry.score, entry.entered_by = value, actor
                entry.save(update_fields=["score", "entered_by", "updated_at"])
                set_count += 1
        return set_count, cleared


class GradeEntry(BaseModel):
    """A student's score on one assessment. A missing row means 'no score yet' (it is not counted as zero)."""

    assessment = models.ForeignKey(Assessment, on_delete=models.PROTECT, related_name="entries")
    enrollment = models.ForeignKey("academics.Enrollment", on_delete=models.PROTECT, related_name="grade_entries")
    score = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0)])
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assessment", "enrollment"], name="uniq_score_per_student")]
        indexes = [models.Index(fields=["enrollment"])]

    def __str__(self):
        return f"{self.enrollment.student}: {self.score:g}/{self.assessment.max_score:g} ({self.assessment.title})"

    def clean(self):
        if self.assessment_id and self.score is not None and self.score > self.assessment.max_score:
            raise ValidationError({"score": f"The score cannot be more than {self.assessment.max_score:g}."})
