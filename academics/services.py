from dataclasses import dataclass

from django.db import transaction

from audit.models import AuditLog
from core.exceptions import DomainError


@dataclass
class PromotionResult:
    promoted: int = 0
    repeated: int = 0
    graduated: int = 0


@transaction.atomic
def promote_section(section, target_section, repeat_students=(), repeat_section=None, actor=None, on=None):
    """Year-end promotion for a whole class.

    Everyone moves to ``target_section`` except ``repeat_students``, who go to
    ``repeat_section`` (same grade, next year). All or nothing: if one student
    cannot be placed (for example the class is full), nobody is changed.
    """
    repeat_ids = {s.pk for s in repeat_students}
    if repeat_ids and repeat_section is None:
        raise DomainError("Choose a class for the students who repeat the year.")
    result = PromotionResult()
    for enrollment in section.active_enrollments().select_related("student"):
        if enrollment.student_id in repeat_ids:
            new = enrollment.repeat(repeat_section, on)
            result.repeated += 1
            action = "repeat"
        else:
            new = enrollment.promote(target_section, on)
            result.promoted += 1
            action = "promote"
        AuditLog.record(actor, action, enrollment, after={"new_enrollment": str(new.pk), "to": str(new.class_section)})
    return result


@transaction.atomic
def graduate_section(section, actor=None, on=None):
    """Close every active enrollment of a final-year class as GRADUATED."""
    result = PromotionResult()
    for enrollment in section.active_enrollments().select_related("student"):
        enrollment.graduate(on)
        AuditLog.record(actor, "graduate", enrollment)
        result.graduated += 1
    return result
