"""The gradebook: all scores of one subject in one class for one term, and the maths on them."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from audit.models import AuditLog
from core.exceptions import DomainError

from .models import Assessment, GradeEntry
from .scales import scale_for

ONE_DP = Decimal("0.1")


def _round(value):
    return None if value is None else value.quantize(ONE_DP, rounding=ROUND_HALF_UP)


def weighted_percent(type_averages, weights):
    """Combine per-type average percentages using each type's weight.

    Types a student has no scores for are left out and the remaining weights are
    rescaled, so a running grade is fair before the exam has been taken.
    """
    parts = [(avg, weights[t]) for t, avg in type_averages.items()]
    total = sum(w for _, w in parts)
    if not parts:
        return None
    if total == 0:
        return sum(avg for avg, _ in parts) / len(parts)
    return sum(avg * w for avg, w in parts) / total


@dataclass
class GradeBook:
    class_subject: object
    term: object
    assessments: list = field(init=False)
    enrollments: list = field(init=False)
    scores: dict = field(init=False)

    def __post_init__(self):
        self.assessments = list(
            Assessment.objects.filter(class_subject=self.class_subject, term=self.term).select_related("assessment_type")
        )
        section = self.class_subject.class_section
        self.enrollments = section.enrollments_between(self.term.starts_on, self.term.ends_on)
        self.scores = {
            (e.assessment_id, e.enrollment_id): e.score
            for e in GradeEntry.objects.filter(assessment__in=self.assessments)
        }

    # --- calculations ----------------------------------------------------
    def score(self, assessment, enrollment):
        return self.scores.get((assessment.pk, enrollment.pk))

    def type_averages(self, enrollment):
        by_type = defaultdict(list)
        for a in self.assessments:
            s = self.score(a, enrollment)
            if s is not None:
                by_type[a.assessment_type_id].append(a.percent(s))
        return {t: sum(v) / len(v) for t, v in by_type.items()}

    def term_percent(self, enrollment):
        weights = {a.assessment_type_id: a.assessment_type.weight_percent for a in self.assessments}
        return _round(weighted_percent(self.type_averages(enrollment), weights))

    def rows(self):
        scale = scale_for(self.class_subject.class_section.academic_year.school)
        rows = []
        for e in self.enrollments:
            percent = self.term_percent(e)
            rows.append({
                "enrollment": e,
                "student": e.student,
                "scores": {a.pk: self.score(a, e) for a in self.assessments},
                "percent": percent,
                "label": scale.label(percent),
                "rank": None,
            })
        _assign_ranks(rows)
        return rows

    # --- saving ----------------------------------------------------------
    @transaction.atomic
    def save_scores(self, actor, matrix):
        """``matrix`` maps (assessment id, enrollment id) -> number or None. All or nothing."""
        if not self.class_subject.can_be_graded_by(actor):
            raise DomainError("You are not allowed to enter grades for this subject.")
        valid = {str(a.pk): a for a in self.assessments}
        grouped = defaultdict(dict)
        for (assessment_id, enrollment_id), value in matrix.items():
            if str(assessment_id) not in valid:
                raise DomainError("One of the assessments does not belong to this subject and term.")
            grouped[str(assessment_id)][enrollment_id] = value
        totals = Counter()
        for assessment_id, scores in grouped.items():
            set_count, cleared = valid[assessment_id].record_scores(scores, actor)
            totals["saved"] += set_count
            totals["cleared"] += cleared
        AuditLog.record(
            actor, "grades_saved", self.class_subject,
            after={"term": self.term.name, "saved": totals["saved"], "cleared": totals["cleared"]},
        )
        return totals


def _assign_ranks(rows, key="percent"):
    """Competition ranking: equal marks share a rank (1, 2, 2, 4). Students without a mark are unranked."""
    ranked = sorted((r for r in rows if r[key] is not None), key=lambda r: r[key], reverse=True)
    previous, rank = None, 0
    for position, row in enumerate(ranked, start=1):
        if row[key] != previous:
            rank, previous = position, row[key]
        row["rank"] = rank
