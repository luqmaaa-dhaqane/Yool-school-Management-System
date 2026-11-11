import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from academics.models import AcademicYear, ClassSection, ClassSubject, Term
from audit.mixins import AuditedAdminMixin
from core.admin_mixins import SchoolScopedAdminMixin
from core.csv_utils import safe_row
from core.exceptions import DomainError

from .forms import MarksheetForm
from .gradebook import GradeBook
from .models import Assessment, AssessmentType, GradeBand, GradeEntry
from .services import class_results


# --- helpers -------------------------------------------------------------
def _terms_for(user):
    qs = Term.objects.select_related("academic_year").order_by("-starts_on")
    if user.is_superuser or not user.school_id:
        return qs if user.is_superuser else qs.none()
    return qs.filter(academic_year__school_id=user.school_id)


def _default_term(user):
    today = date.today()
    terms = list(_terms_for(user))
    for t in terms:
        if t.contains(today):
            return t
    started = [t for t in terms if t.starts_on <= today]
    return started[0] if started else (terms[0] if terms else None)


def _class_subjects_for(user, term):
    """Subject classes this user can open in the gradebook for a term's academic year."""
    qs = ClassSubject.objects.filter(class_section__academic_year=term.academic_year).select_related(
        "class_section__grade_level", "class_section__academic_year", "subject", "teacher"
    )
    if user.is_superuser:
        pass
    elif user.role in ("principal", "registrar") and user.school_id:
        qs = qs.filter(class_section__academic_year__school_id=user.school_id)
    elif user.role == "teacher":
        qs = qs.filter(teacher=user)
    else:
        return qs.none()
    return qs.order_by("class_section__grade_level__sort_order", "class_section__name", "subject__name")


def _fmt(value):
    """A score without pointless zeros: 18.00 -> 18, 12.50 -> 12.5."""
    return "" if value is None else format(Decimal(value).normalize(), "f")


def _pct(value):
    return "" if value is None else f"{value:.1f}"


# --- simple setup models ---------------------------------------------------
@admin.register(AssessmentType)
class AssessmentTypeAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "weight_percent")

    def changelist_view(self, request, extra_context=None):
        school_id = request.user.school_id
        if school_id:
            total = sum(
                AssessmentType.objects.filter(school_id=school_id).values_list("weight_percent", flat=True)
            ) or Decimal(0)
            if total != 100:
                self.message_user(
                    request,
                    f"The weights currently add up to {total:g}%, not 100%. "
                    "Grades are still calculated, but the weights should total 100.",
                    level=messages.WARNING,
                )
        return super().changelist_view(request, extra_context)


@admin.register(GradeBand)
class GradeBandAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("label", "min_percent", "remark")


@admin.register(GradeEntry)
class GradeEntryAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    """Read-only list of individual scores (grades are entered on the gradebook page)."""

    school_lookup = "assessment__class_subject__class_section__academic_year__school"
    list_display = ("enrollment", "assessment", "score", "entered_by", "updated_at")
    list_filter = ("assessment__term",)
    search_fields = ("enrollment__student__first_name", "enrollment__student__last_name")
    list_select_related = ("enrollment__student", "assessment__class_subject__subject", "entered_by")

    def scope_for_teacher(self, qs, user):
        return qs.filter(assessment__class_subject__teacher=user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# --- assessments + the gradebook pages ----------------------------------------
@admin.register(Assessment)
class AssessmentAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    school_lookup = "class_subject__class_section__academic_year__school"
    list_display = ("title", "class_subject", "term", "assessment_type", "max_score", "held_on")
    list_filter = ("term", "assessment_type")
    list_select_related = ("class_subject__class_section__grade_level", "class_subject__subject", "term", "assessment_type")

    def scope_for_teacher(self, qs, user):
        return qs.filter(class_subject__teacher=user)

    def has_change_permission(self, request, obj=None):
        if obj is not None and not obj.term.is_open():
            return False  # locked term: view only
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not obj.term.is_open():
            return False
        return super().has_delete_permission(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        user = request.user
        if not user.is_superuser and user.school_id:
            if db_field.name == "class_subject":
                kwargs["queryset"] = ClassSubject.objects.filter(
                    class_section__academic_year__school_id=user.school_id
                ).select_related("class_section__grade_level", "subject")
                if user.role == "teacher":
                    kwargs["queryset"] = kwargs["queryset"].filter(teacher=user)
            elif db_field.name == "term":
                kwargs["queryset"] = _terms_for(user)
            elif db_field.name == "assessment_type":
                kwargs["queryset"] = AssessmentType.objects.filter(school_id=user.school_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("grid/<uuid:class_subject_id>/<uuid:term_id>/", wrap(self.grid_view), name="grades_grid"),
            path("reports/marksheet/", wrap(self.marksheet_view), name="grades_marksheet"),
        ]
        return custom + super().get_urls()

    # --- dashboard -------------------------------------------------------
    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        terms = list(_terms_for(request.user))
        term = None
        if request.GET.get("term"):
            term = next((t for t in terms if str(t.pk) == request.GET["term"]), None)
        term = term or _default_term(request.user)
        rows = []
        if term:
            subjects = list(_class_subjects_for(request.user, term))
            counts = dict(
                Assessment.objects.filter(term=term, class_subject__in=subjects)
                .values_list("class_subject").annotate(n=Count("id")).values_list("class_subject", "n")
            )
            entered = dict(
                GradeEntry.objects.filter(assessment__term=term, assessment__class_subject__in=subjects)
                .values_list("assessment__class_subject").annotate(n=Count("id")).values_list("assessment__class_subject", "n")
            )
            students = dict(
                ClassSection.objects.filter(pk__in={s.class_section_id for s in subjects})
                .annotate(n=Count(
                    "enrollments",
                    filter=Q(enrollments__enrolled_on__lte=term.ends_on)
                    & (Q(enrollments__left_on__isnull=True) | Q(enrollments__left_on__gte=term.starts_on)),
                ))
                .values_list("pk", "n")
            )
            for cs in subjects:
                n_students, n_assess = students.get(cs.class_section_id, 0), counts.get(cs.pk, 0)
                rows.append({
                    "cs": cs, "assessments": n_assess, "students": n_students,
                    "entered": entered.get(cs.pk, 0), "expected": n_students * n_assess,
                    "can_grade": cs.can_be_graded_by(request.user),
                })
        context = {
            **self.admin_site.each_context(request),
            "title": "Gradebook",
            "terms": terms,
            "term": term,
            "rows": rows,
            "can_add": self.has_add_permission(request),
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/grades/gradebook.html", context)

    # --- the grid --------------------------------------------------------
    def grid_view(self, request, class_subject_id, term_id):
        if not self.has_view_permission(request):
            raise PermissionDenied
        term = get_object_or_404(_terms_for(request.user), pk=term_id)
        class_subject = get_object_or_404(_class_subjects_for(request.user, term), pk=class_subject_id)
        user = request.user
        editable = (
            user.has_perm("grades.change_gradeentry") or user.has_perm("grades.add_gradeentry")
        ) and class_subject.can_be_graded_by(user) and term.is_open()
        book = GradeBook(class_subject, term)
        posted = request.POST if request.method == "POST" and editable else None

        if posted is not None:
            matrix, errors = {}, []
            for a in book.assessments:
                for e in book.enrollments:
                    key = f"score_{a.pk}_{e.pk}"
                    if key not in posted:
                        continue
                    raw = posted[key].strip().replace(",", ".")
                    if raw == "":
                        matrix[(a.pk, e.pk)] = None
                        continue
                    try:
                        matrix[(a.pk, e.pk)] = Decimal(raw)
                    except InvalidOperation:
                        errors.append(f"{e.student.full_name} / {a.title}: '{posted[key]}' is not a number.")
            if not errors:
                try:
                    totals = book.save_scores(user, matrix)
                except DomainError as exc:
                    errors.append(str(exc))
                else:
                    self.message_user(
                        request, f"Grades saved ({totals['saved']} entered or changed, {totals['cleared']} cleared).",
                        level=messages.SUCCESS,
                    )
                    return redirect(request.path)
            for message in errors[:10]:
                messages.error(request, message)

        rows = book.rows()
        for row in rows:
            row["cells"] = []
            for a in book.assessments:
                if posted is not None and f"score_{a.pk}_{row['enrollment'].pk}" in posted:
                    value = posted[f"score_{a.pk}_{row['enrollment'].pk}"]
                else:
                    value = _fmt(row["scores"][a.pk])
                row["cells"].append({"assessment": a, "value": value, "name": f"score_{a.pk}_{row['enrollment'].pk}"})
        context = {
            **self.admin_site.each_context(request),
            "title": f"{class_subject.subject} - {class_subject.class_section} - {term.name}",
            "class_subject": class_subject,
            "term": term,
            "assessments": book.assessments,
            "rows": rows,
            "editable": editable,
            "locked": not term.is_open(),
            "can_add": self.has_add_permission(request) and class_subject.can_be_graded_by(user) and term.is_open(),
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/grades/grid.html", context)

    # --- class mark sheet ------------------------------------------------
    def marksheet_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        user = request.user
        terms = _terms_for(user)
        sections = user.managed_sections().select_related("grade_level", "academic_year")
        form = MarksheetForm(request.GET or None, terms=terms, sections=sections)
        header, rows, name = [], None, "marksheet.csv"
        if request.GET and form.is_valid():
            term, section = form.cleaned_data["term"], form.cleaned_data["section"]
            if section.academic_year_id != term.academic_year_id:
                form.add_error("term", "This term belongs to a different academic year than the class.")
            else:
                data = class_results(section, term)
                header = ["Rank", "Student no", "Student"] + [str(cs.subject) for cs in data["subjects"]] + [
                    "Overall %", "Grade"]
                rows = [
                    [r["rank"] or "", r["student"].student_no, r["student"].full_name]
                    + [_pct(r["percents"][cs.pk]) for cs in data["subjects"]]
                    + [_pct(r["overall"]), r["label"]]
                    for r in sorted(data["rows"], key=lambda r: (r["rank"] is None, r["rank"] or 0, r["student"].last_name))
                ]
                name = f"marksheet-{section.display_name}-{term.name}.csv".replace(" ", "-")
        if rows is not None and request.GET.get("format") == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="{name}"'
            writer = csv.writer(response)
            writer.writerow(header)
            for row in rows:
                writer.writerow(safe_row(row))
            return response
        context = {
            **self.admin_site.each_context(request),
            "title": "Class mark sheet",
            "form": form, "header": header, "rows": rows,
            "query": request.GET.urlencode(),
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/grades/report.html", context)
