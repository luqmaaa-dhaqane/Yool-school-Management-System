"""Everything that goes on a report card, assembled once for a whole class and term."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from academics.models import ClassSubject
from attendance.models import AttendanceRegister
from attendance.services import Summary, student_rows
from grades.gradebook import GradeBook, _assign_ranks, _round
from grades.models import AssessmentType
from grades.scales import BandScale, scale_for

from .models import ReportComment


@dataclass
class SubjectLine:
    subject: str
    teacher: str
    by_type: dict           # assessment type name -> average percent (or None)
    percent: Decimal | None
    label: str
    remark: str
    rank: int | None
    ranked_students: int


@dataclass
class ReportCard:
    school: object
    term: object
    section: object
    enrollment: object
    student: object
    homeroom_teacher: str
    type_names: list
    lines: list
    overall: Decimal | None
    overall_label: str
    overall_remark: str
    rank: int | None
    class_size: int
    attendance: Summary
    days_open: int
    teacher_comment: str
    principal_comment: str
    grade_key: list = field(default_factory=list)   # [(label, "90-100", remark)]
    provisional: bool = False                       # the term is not locked yet: grades can still change

    @property
    def has_grades(self):
        return any(line.percent is not None for line in self.lines)


def _plain(number):
    """90.00 -> '90', 89.9 -> '89.9'."""
    return format(Decimal(number).normalize(), "f")


def grade_key(school):
    """The grading scale as readable ranges, for the bottom of the card."""
    scale = scale_for(school)
    if not isinstance(scale, BandScale):
        return []
    whole = all(Decimal(b.min_percent) == Decimal(b.min_percent).to_integral_value() for b in scale.bands)
    step = Decimal(1) if whole else Decimal("0.1")
    key, upper = [], Decimal(100)
    for band in scale.bands:  # highest first
        key.append((band.label, f"{_plain(band.min_percent)}-{_plain(upper)}", band.remark))
        upper = Decimal(band.min_percent) - step
    return key


def build_report_cards(section, term, enrollment_ids=None):
    """Report cards for every student of the class (or just the given enrollments) for one term."""
    school = section.academic_year.school
    scale = scale_for(school)
    enrollments = section.enrollments_between(term.starts_on, term.ends_on)
    class_subjects = ClassSubject.objects.filter(class_section=section).select_related("subject", "teacher")
    books = [b for b in (GradeBook(cs, term) for cs in class_subjects) if b.assessments]
    types = list(AssessmentType.objects.filter(school=school).order_by("-weight_percent", "name"))
    used_type_ids = {a.assessment_type_id for b in books for a in b.assessments}
    types = [t for t in types if t.pk in used_type_ids]

    # per subject: every student's row (percent, rank) computed once
    subject_rows = {b.class_subject.pk: {r["enrollment"].pk: r for r in b.rows()} for b in books}

    overall_rows = []
    for e in enrollments:
        percents = [subject_rows[b.class_subject.pk][e.pk]["percent"] for b in books]
        present = [p for p in percents if p is not None]
        overall_rows.append({"enrollment": e, "overall": _round(sum(present) / len(present)) if present else None, "rank": None})
    _assign_ranks(overall_rows, key="overall")
    overall_by_enrollment = {r["enrollment"].pk: r for r in overall_rows}

    attendance = {r["enrollment"].pk: r["summary"] for r in student_rows(section, term.starts_on, term.ends_on)}
    days_open = AttendanceRegister.objects.filter(class_section=section, date__gte=term.starts_on, date__lte=term.ends_on).count()
    comments = {c.enrollment_id: c for c in ReportComment.objects.filter(term=term, enrollment__in=enrollments)}
    homeroom = section.homeroom_teacher.get_full_name() or section.homeroom_teacher.username if section.homeroom_teacher else ""
    key = grade_key(school)

    cards = []
    for e in enrollments:
        if enrollment_ids is not None and e.pk not in enrollment_ids:
            continue
        lines = []
        for b in books:
            row = subject_rows[b.class_subject.pk][e.pk]
            averages = b.type_averages(e)
            lines.append(SubjectLine(
                subject=b.class_subject.subject.name,
                teacher=(b.class_subject.teacher.get_full_name() or b.class_subject.teacher.username) if b.class_subject.teacher else "",
                by_type={t.name: _round(averages[t.pk]) if t.pk in averages else None for t in types},
                percent=row["percent"], label=row["label"], remark=scale.remark(row["percent"]),
                rank=row["rank"], ranked_students=sum(1 for r in subject_rows[b.class_subject.pk].values() if r["percent"] is not None),
            ))
        overall = overall_by_enrollment[e.pk]
        comment = comments.get(e.pk)
        cards.append(ReportCard(
            school=school, term=term, section=section, enrollment=e, student=e.student, homeroom_teacher=homeroom,
            type_names=[t.name for t in types], lines=lines,
            overall=overall["overall"], overall_label=scale.label(overall["overall"]), overall_remark=scale.remark(overall["overall"]),
            rank=overall["rank"], class_size=sum(1 for r in overall_rows if r["overall"] is not None),
            attendance=attendance.get(e.pk, Summary()), days_open=days_open,
            teacher_comment=comment.teacher_comment if comment else "",
            principal_comment=comment.principal_comment if comment else "",
            grade_key=key, provisional=not term.is_locked,
        ))
    return cards
