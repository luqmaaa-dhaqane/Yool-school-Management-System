import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from academics.models import ClassSection, ClassSubject
from academics.selectors import default_term, terms_for
from audit.mixins import AuditedAdminMixin
from audit.models import AuditLog
from core.admin_mixins import SchoolScopedAdminMixin
from core.csv_utils import safe_row
from core.exceptions import DomainError

from .forms import MarksheetForm
from .gradebook import GradeBook
from .models import Assessment, AssessmentType, GradeBand, GradeEntry
from .services import class_results


# --- helpers -------------------------------------------------------------
_terms_for, _default_term = terms_for, default_term


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
    return qs.order_by("class_section__grade_level__sort_order", "subject__name")


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
            path("quick/add/<uuid:class_subject_id>/<uuid:term_id>/", wrap(self.quick_add_view), name="grades_quick_add"),
            path("quick/<uuid:assessment_id>/edit/", wrap(self.quick_edit_view), name="grades_quick_edit"),
            path("quick/<uuid:assessment_id>/delete/", wrap(self.quick_delete_view), name="grades_quick_delete"),
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
            "types": AssessmentType.objects.filter(school_id=request.user.school_id) if request.user.school_id else AssessmentType.objects.all(),
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
            "types": AssessmentType.objects.filter(school=class_subject.class_section.academic_year.school),
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

    # --- quick add / edit / delete from the gradebook (dialogs, no separate page) ----
    def _read_assessment_form(self, request, class_subject):
        school = class_subject.class_section.academic_year.school
        title = request.POST.get("title", "").strip()
        if not title:
            raise DomainError("Please give the assessment a title.")
        try:
            kind = AssessmentType.objects.filter(pk=request.POST.get("assessment_type"), school=school).first()
        except (ValidationError, ValueError):
            kind = None
        if kind is None:
            raise DomainError("Choose an assessment type.")
        try:
            maximum = Decimal(request.POST.get("max_score", "").strip().replace(",", "."))
        except InvalidOperation:
            raise DomainError("The maximum score must be a number.")
        if maximum <= 0:
            raise DomainError("The maximum score must be more than zero.")
        held_raw = request.POST.get("held_on", "").strip()
        try:
            held_on = date.fromisoformat(held_raw) if held_raw else None
        except ValueError:
            raise DomainError("The date is not valid.")
        return title, kind, maximum, held_on

    def _go_back(self, class_subject, term):
        return redirect(reverse("admin:grades_grid", args=[class_subject.pk, term.pk]))

    def quick_add_view(self, request, class_subject_id, term_id):
        user = request.user
        term = get_object_or_404(_terms_for(user), pk=term_id)
        cs = get_object_or_404(_class_subjects_for(user, term), pk=class_subject_id)
        if request.method != "POST":
            return self._go_back(cs, term)
        if not (user.has_perm("grades.add_assessment") and cs.can_be_graded_by(user)):
            raise PermissionDenied
        try:
            title, kind, maximum, held_on = self._read_assessment_form(request, cs)
            if Assessment.objects.filter(class_subject=cs, term=term, title=title).exists():
                raise DomainError(f"An assessment called '{title}' already exists for this subject and term.")
            assessment = Assessment(class_subject=cs, term=term, assessment_type=kind, title=title, max_score=maximum, held_on=held_on)
            assessment.full_clean()
            assessment.save()
        except DomainError as exc:
            messages.error(request, str(exc))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            AuditLog.record(user, "create", assessment, after={"title": title, "max_score": str(maximum), "term": term.name})
            self.message_user(request, f"'{title}' added. You can enter scores now.", level=messages.SUCCESS)
        return self._go_back(cs, term)

    def _assessment_for(self, request, assessment_id):
        assessment = get_object_or_404(
            Assessment.objects.select_related("class_subject__class_section__academic_year__school", "term", "assessment_type"),
            pk=assessment_id,
        )
        cs = get_object_or_404(_class_subjects_for(request.user, assessment.term), pk=assessment.class_subject_id)
        return assessment, cs

    def quick_edit_view(self, request, assessment_id):
        user = request.user
        assessment, cs = self._assessment_for(request, assessment_id)
        if request.method != "POST":
            return self._go_back(cs, assessment.term)
        if not (user.has_perm("grades.change_assessment") and cs.can_be_graded_by(user)):
            raise PermissionDenied
        try:
            title, kind, maximum, held_on = self._read_assessment_form(request, cs)
            if Assessment.objects.filter(class_subject=cs, term=assessment.term, title=title).exclude(pk=assessment.pk).exists():
                raise DomainError(f"An assessment called '{title}' already exists for this subject and term.")
            if assessment.entries.filter(score__gt=maximum).exists():
                raise DomainError("Some students already have a score higher than that maximum. Correct those scores first.")
            assessment.title, assessment.assessment_type, assessment.max_score, assessment.held_on = title, kind, maximum, held_on
            assessment.full_clean()
            assessment.save()
        except DomainError as exc:
            messages.error(request, str(exc))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            AuditLog.record(user, "update", assessment, after={"title": title, "max_score": str(maximum)})
            self.message_user(request, f"'{title}' saved.", level=messages.SUCCESS)
        return self._go_back(cs, assessment.term)

    def quick_delete_view(self, request, assessment_id):
        user = request.user
        assessment, cs = self._assessment_for(request, assessment_id)
        if request.method != "POST":
            return self._go_back(cs, assessment.term)
        if not (user.has_perm("grades.delete_assessment") and cs.can_be_graded_by(user)):
            raise PermissionDenied
        title = assessment.title
        try:
            if assessment.entries.exists():
                raise DomainError("This assessment already has scores. Clear the scores first, then delete it.")
            AuditLog.record(user, "delete", assessment, before={"title": title})
            assessment.delete()
        except DomainError as exc:
            messages.error(request, str(exc))
        else:
            self.message_user(request, f"'{title}' deleted.", level=messages.SUCCESS)
        return self._go_back(cs, assessment.term)
