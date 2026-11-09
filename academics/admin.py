import csv

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from audit.mixins import AuditedAdminMixin
from audit.models import AuditLog
from core.admin_mixins import SchoolScopedAdminMixin
from core.csv_utils import safe_row
from core.exceptions import DomainError

from .forms import PromoteForm
from .models import AcademicYear, ClassSection, Enrollment, GradeLevel, Subject, Term
from .services import graduate_section, promote_section


class TermInline(admin.TabularInline):
    model = Term
    extra = 0


@admin.register(AcademicYear)
class AcademicYearAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "starts_on", "ends_on", "is_current", "is_locked")
    inlines = [TermInline]


@admin.register(GradeLevel)
class GradeLevelAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "sort_order")


@admin.register(Subject)
class SubjectAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "name_so")
    search_fields = ("code", "name")


@admin.register(ClassSection)
class ClassSectionAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    school_lookup = "academic_year__school"
    list_display = ("display_name", "academic_year", "homeroom_teacher", "students", "capacity", "roster_link")
    list_filter = ("academic_year", "grade_level")
    actions = ["promote_action", "graduate_action"]

    def scope_for_teacher(self, qs, user):
        return qs.filter(homeroom_teacher=user)

    @admin.display(description="Students")
    def students(self, obj):
        return obj.enrolled_count()

    @admin.display(description="Roster")
    def roster_link(self, obj):
        url = reverse("admin:academics_classsection_roster", args=[obj.pk])
        return format_html('<a href="{}">View / print</a>', url)

    # --- actions ---------------------------------------------------------
    @admin.action(description="Promote this class to next year...", permissions=["change"])
    def promote_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one class to promote.", level=messages.ERROR)
            return None
        return redirect(reverse("admin:academics_classsection_promote", args=[queryset.first().pk]))

    @admin.action(description="Graduate this (final-year) class", permissions=["change"])
    def graduate_action(self, request, queryset):
        try:
            total = sum(graduate_section(s, request.user).graduated for s in queryset)
        except DomainError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return
        self.message_user(request, f"{total} student(s) marked as graduated.", level=messages.SUCCESS)

    # --- custom pages ----------------------------------------------------
    def get_urls(self):
        custom = [
            path("<uuid:pk>/promote/", self.admin_site.admin_view(self.promote_view),
                 name="academics_classsection_promote"),
            path("<uuid:pk>/roster/", self.admin_site.admin_view(self.roster_view),
                 name="academics_classsection_roster"),
        ]
        return custom + super().get_urls()

    def promote_view(self, request, pk):
        if not (request.user.has_perm("academics.change_enrollment")
                and request.user.has_perm("academics.add_enrollment")):
            raise PermissionDenied
        section = get_object_or_404(self.get_queryset(request), pk=pk)
        form = PromoteForm(section, request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                result = promote_section(
                    section,
                    form.cleaned_data["target_section"],
                    form.cleaned_data["repeat_students"],
                    form.cleaned_data["repeat_section"],
                    actor=request.user,
                )
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(
                    request,
                    f"{result.promoted} promoted, {result.repeated} repeating.",
                    level=messages.SUCCESS,
                )
                return redirect("admin:academics_classsection_changelist")
        context = {
            **self.admin_site.each_context(request),
            "title": f"Promote {section}",
            "section": section,
            "form": form,
            "student_count": section.enrolled_count(),
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/academics/promote.html", context)

    def roster_view(self, request, pk):
        if not self.has_view_permission(request):
            raise PermissionDenied
        section = get_object_or_404(self.get_queryset(request), pk=pk)
        rows = []
        for number, enrollment in enumerate(section.roster(), start=1):
            student = enrollment.student
            guardian = student.primary_guardian()
            rows.append({
                "n": number,
                "student_no": student.student_no,
                "name": student.full_name,
                "gender": student.get_gender_display(),
                "age": student.age(),
                "guardian": guardian.full_name if guardian else "",
                "phone": guardian.phone if guardian else "",
            })
        if request.GET.get("format") == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="roster-{section.display_name}.csv"'
            writer = csv.writer(response)
            writer.writerow(["#", "Student no", "Name", "Gender", "Age", "Guardian", "Guardian phone"])
            for r in rows:
                writer.writerow(safe_row([r["n"], r["student_no"], r["name"], r["gender"], r["age"], r["guardian"], r["phone"]]))
            return response
        context = {
            **self.admin_site.each_context(request),
            "title": f"Roster: {section} ({section.academic_year})",
            "section": section,
            "rows": rows,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/academics/roster.html", context)


@admin.register(Enrollment)
class EnrollmentAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    school_lookup = "class_section__academic_year__school"
    list_display = ("student", "class_section", "academic_year", "status", "enrolled_on", "left_on")
    list_filter = ("academic_year", "status", "class_section__grade_level")
    search_fields = ("student__first_name", "student__last_name", "student__student_no")
    autocomplete_fields = ("student",)
    list_select_related = ("student", "class_section__grade_level", "academic_year")
    actions = ["withdraw_action", "transfer_action", "graduate_action"]

    def scope_for_teacher(self, qs, user):
        return qs.filter(class_section__homeroom_teacher=user)

    fields = ("student", "class_section", "enrolled_on", "notes", "status", "left_on", "academic_year")

    def get_readonly_fields(self, request, obj=None):
        base = ("status", "left_on", "academic_year")  # changed only through actions
        return base + ("student",) if obj else base

    def _apply(self, request, queryset, method, label, audit_action):
        done = 0
        for enrollment in queryset.select_related("student"):
            try:
                getattr(enrollment, method)()
            except DomainError as exc:
                self.message_user(request, f"{enrollment.student}: {exc}", level=messages.ERROR)
            else:
                AuditLog.record(request.user, audit_action, enrollment)
                done += 1
        if done:
            self.message_user(request, f"{done} student(s) {label}.", level=messages.SUCCESS)

    @admin.action(description="Withdraw selected students", permissions=["change"])
    def withdraw_action(self, request, queryset):
        self._apply(request, queryset, "withdraw", "withdrawn", "withdraw")

    @admin.action(description="Mark selected students as transferred out", permissions=["change"])
    def transfer_action(self, request, queryset):
        self._apply(request, queryset, "transfer_out", "transferred out", "transfer_out")

    @admin.action(description="Mark selected students as graduated", permissions=["change"])
    def graduate_action(self, request, queryset):
        self._apply(request, queryset, "graduate", "graduated", "graduate")
