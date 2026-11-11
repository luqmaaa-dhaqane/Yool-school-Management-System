"""Results across all subjects of a class (the mark sheet; report cards build on this)."""
from academics.models import ClassSubject

from .gradebook import GradeBook, _assign_ranks, _round
from .scales import scale_for


def class_results(section, term):
    """Every student of the class with a percentage per subject, an overall average, grade and rank.

    Only subjects that have at least one assessment in the term are included.
    The overall average is the plain average of the subject percentages the student has.
    """
    class_subjects = ClassSubject.objects.filter(class_section=section).select_related("subject", "teacher")
    books = [GradeBook(cs, term) for cs in class_subjects]
    books = [b for b in books if b.assessments]
    enrollments = section.enrollments_between(term.starts_on, term.ends_on)
    scale = scale_for(section.academic_year.school)
    rows = []
    for e in enrollments:
        percents = {b.class_subject.pk: b.term_percent(e) for b in books}
        present = [p for p in percents.values() if p is not None]
        overall = _round(sum(present) / len(present)) if present else None
        rows.append({
            "enrollment": e, "student": e.student, "percents": percents,
            "overall": overall, "label": scale.label(overall), "rank": None,
        })
    _assign_ranks(rows, key="overall")
    return {"subjects": [b.class_subject for b in books], "rows": rows}
