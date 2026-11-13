from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from audit.models import AuditLog
from core.exceptions import DomainError
from core.models import BaseModel

MAX_COMMENT = 500


def can_write_teacher_comment(user, section, term):
    """Class teacher comments: the homeroom teacher while the term is open; the Principal and super admin any time."""
    if user.is_superuser:
        return True
    if user.school_id != section.academic_year.school_id:
        return False
    if user.role == "principal":
        return True
    return user.role == "teacher" and section.homeroom_teacher_id == user.pk and term.is_open()


def can_write_principal_comment(user, section):
    if user.is_superuser:
        return True
    return user.role == "principal" and user.school_id == section.academic_year.school_id


class ReportComment(BaseModel):
    """What the class teacher and the Principal write on a student's report card for a term."""

    enrollment = models.ForeignKey("academics.Enrollment", on_delete=models.PROTECT, related_name="report_comments")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="report_comments")
    teacher_comment = models.TextField(blank=True, max_length=MAX_COMMENT)
    principal_comment = models.TextField(blank=True, max_length=MAX_COMMENT)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["enrollment", "term"], name="one_comment_row_per_student_term")]

    def __str__(self):
        return f"{self.enrollment.student} - {self.term}"

    def clean(self):
        if self.enrollment_id and self.term_id and self.enrollment.academic_year_id != self.term.academic_year_id:
            raise ValidationError("The term belongs to a different academic year than the student's class.")

    @classmethod
    @transaction.atomic
    def write(cls, enrollment, term, actor, teacher_comment=None, principal_comment=None):
        """Save comments. ``None`` leaves that comment untouched. Who may write which one is enforced here."""
        section = enrollment.class_section
        if term.academic_year_id != enrollment.academic_year_id:
            raise DomainError("The term belongs to a different academic year than the student's class.")
        row = cls.objects.filter(enrollment=enrollment, term=term).first() or cls(enrollment=enrollment, term=term)
        changed = {}
        for field, value, allowed in (
            ("teacher_comment", teacher_comment, can_write_teacher_comment(actor, section, term)),
            ("principal_comment", principal_comment, can_write_principal_comment(actor, section)),
        ):
            if value is None:
                continue
            value = value.strip()
            if value == getattr(row, field):
                continue
            if not allowed:
                who = "the class teacher" if field == "teacher_comment" else "the Principal"
                raise DomainError(f"You are not allowed to write {who}'s comment (or the term is locked).")
            if len(value) > MAX_COMMENT:
                raise DomainError(f"A comment can be at most {MAX_COMMENT} characters.")
            setattr(row, field, value)
            changed[field] = True
        if changed:
            row.updated_by = actor
            row.save()
            AuditLog.record(actor, "report_comment", row, after={"term": term.name, "fields": sorted(changed)})
        return row
