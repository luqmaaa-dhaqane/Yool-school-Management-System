"""One-time grading set-up for a school. Safe to run again: it only adds what is missing."""
from academics.models import AcademicYear, ClassSection

from .models import AssessmentType, GradeBand

DEFAULT_TYPES = [("Quiz", 20), ("Midterm", 30), ("Exam", 50)]
DEFAULT_BANDS = [
    ("A", 90, "Excellent"), ("B", 80, "Very good"), ("C", 70, "Good"),
    ("D", 60, "Satisfactory"), ("E", 50, "Pass"), ("F", 0, "Needs improvement"),
]


def ensure_grading_setup(school):
    """Weights that add up to 100, letter bands A-F, and every subject on every class of the current year."""
    for name, weight in DEFAULT_TYPES:
        AssessmentType.objects.get_or_create(school=school, name=name, defaults={"weight_percent": weight})
    for label, minimum, remark in DEFAULT_BANDS:
        GradeBand.objects.get_or_create(school=school, label=label, defaults={"min_percent": minimum, "remark": remark})
    added = 0
    year = AcademicYear.objects.filter(school=school, is_current=True).first()
    if year:
        for section in ClassSection.objects.filter(academic_year=year):
            added += section.add_all_subjects()
    return added
