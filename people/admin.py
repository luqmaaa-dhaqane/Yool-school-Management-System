from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path

from academics.models import Enrollment
from audit.mixins import AuditedAdminMixin
from audit.models import AuditLog
from core.admin_mixins import SchoolScopedAdminMixin
from core.exceptions import DomainError

from .importers import TEMPLATE_HEADER, TEMPLATE_ROWS, StudentImporter
from .models import Guardian, Student, StudentGuardian


class StudentGuardianInline(admin.TabularInline):
    model = StudentGuardian
    extra = 1


class EnrollmentInline(admin.TabularInline):
    """Place a student in a class from the student's own page."""

    model = Enrollment
    fields = ("class_section", "enrolled_on", "status", "left_on")
    readonly_fields = ("status", "left_on")
    extra = 0
    verbose_name_plural = "Class enrollments"


class ImportForm(forms.Form):
    file = forms.FileField(label="CSV file")
    preview_only = forms.BooleanField(
        required=False, initial=True, label="Preview only (check for errors, do not save)"
    )


@admin.register(Student)
class StudentAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("student_no", "full_name", "gender", "current_class_display", "status", "admitted_on")
    list_filter = ("status", "gender")
    search_fields = ("student_no", "first_name", "last_name")
    readonly_fields = ("student_no",)
    inlines = [StudentGuardianInline, EnrollmentInline]

    def scope_for_teacher(self, qs, user):
        return qs.filter(
            enrollments__status="enrolled", enrollments__class_section__homeroom_teacher=user
        ).distinct()

    @admin.display(description="Class")
    def current_class_display(self, obj):
        return obj.current_class() or "-"

    def get_exclude(self, request, obj=None):
        # Medical notes are restricted to roles holding the dedicated permission.
        if not request.user.has_perm("people.view_medical_notes"):
            return ["medical_notes"]
        return super().get_exclude(request, obj)

    def save_formset(self, request, form, formset, change):
        try:
            super().save_formset(request, form, formset, change)
        except DomainError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)

    # --- CSV import ------------------------------------------------------
    def get_urls(self):
        custom = [
            path("import/", self.admin_site.admin_view(self.import_view), name="people_student_import"),
            path("import/template/", self.admin_site.admin_view(self.template_view),
                 name="people_student_import_template"),
        ]
        return custom + super().get_urls()

    def template_view(self, request):
        import csv

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="students-template.csv"'
        writer = csv.writer(response)
        writer.writerow(TEMPLATE_HEADER)
        writer.writerows(TEMPLATE_ROWS)
        return response

    def import_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        school = request.user.school
        form = ImportForm(request.POST or None, request.FILES or None)
        report = None
        if school is None:
            messages.error(request, "Your account is not linked to a school.")
        elif request.method == "POST" and form.is_valid():
            report = StudentImporter(school).run(
                form.cleaned_data["file"], dry_run=form.cleaned_data["preview_only"]
            )
            if report.committed:
                AuditLog.record(
                    request.user, "import", school,
                    after={"students": report.created, "enrolled": report.enrolled},
                )
                self.message_user(request, f"Imported {report.created} students.", level=messages.SUCCESS)
        context = {
            **self.admin_site.each_context(request),
            "title": "Import students from CSV",
            "form": form,
            "report": report,
            "columns": TEMPLATE_HEADER,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/people/import_students.html", context)


@admin.register(Guardian)
class GuardianAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("full_name", "phone", "email")
    search_fields = ("first_name", "last_name", "phone", "email")

    def scope_for_teacher(self, qs, user):
        return qs.filter(
            student_links__student__enrollments__status="enrolled",
            student_links__student__enrollments__class_section__homeroom_teacher=user,
        ).distinct()
