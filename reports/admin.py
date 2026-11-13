import re

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from academics.selectors import default_term, terms_for
from audit.models import AuditLog
from core.admin_mixins import SchoolScopedAdminMixin
from core.exceptions import DomainError

from .models import MAX_COMMENT, ReportComment, can_write_principal_comment, can_write_teacher_comment
from .pdf import render_report_cards
from .services import build_report_cards


def _filename(*parts):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", "-".join(parts)).strip("-") + ".pdf"


@admin.register(ReportComment)
class ReportCardAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    """The 'Report cards' entry: pick a term, write comments, download the PDFs."""

    school_lookup = "enrollment__class_section__academic_year__school"

    def scope_for_teacher(self, qs, user):
        return qs.filter(enrollment__class_section__homeroom_teacher=user)

    # report comments are written on the comments page, never edited as raw rows
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("comments/<uuid:section_id>/<uuid:term_id>/", wrap(self.comments_view), name="reports_comments"),
            path("pdf/<uuid:section_id>/<uuid:term_id>/", wrap(self.pdf_view), name="reports_pdf_class"),
            path("pdf/<uuid:section_id>/<uuid:term_id>/<uuid:enrollment_id>/", wrap(self.pdf_view), name="reports_pdf_student"),
        ]
        return custom + super().get_urls()

    # --- helpers -----------------------------------------------------------
    def _section_and_term(self, request, section_id, term_id):
        term = get_object_or_404(terms_for(request.user), pk=term_id)
        section = get_object_or_404(
            request.user.managed_sections().select_related("grade_level", "academic_year__school", "homeroom_teacher"), pk=section_id
        )
        if section.academic_year_id != term.academic_year_id:
            raise PermissionDenied
        return section, term

    # --- dashboard ---------------------------------------------------------
    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        terms = list(terms_for(request.user))
        term = next((t for t in terms if str(t.pk) == request.GET.get("term")), None) or default_term(request.user)
        rows = []
        if term:
            sections = request.user.managed_sections().filter(academic_year=term.academic_year).select_related(
                "grade_level", "homeroom_teacher"
            ).order_by("grade_level__sort_order")
            for section in sections:
                enrollments = section.enrollments_between(term.starts_on, term.ends_on)
                comments = ReportComment.objects.filter(term=term, enrollment__in=enrollments)
                rows.append({
                    "section": section, "students": len(enrollments),
                    "teacher_comments": comments.exclude(teacher_comment="").count(),
                    "principal_comments": comments.exclude(principal_comment="").count(),
                })
        context = {**self.admin_site.each_context(request), "title": "Report cards", "terms": terms, "term": term,
                   "rows": rows, "opts": self.model._meta}
        return TemplateResponse(request, "admin/reports/dashboard.html", context)

    # --- comments ------------------------------------------------------------
    def comments_view(self, request, section_id, term_id):
        if not self.has_view_permission(request):
            raise PermissionDenied
        section, term = self._section_and_term(request, section_id, term_id)
        user = request.user
        may_teacher = can_write_teacher_comment(user, section, term)
        may_principal = can_write_principal_comment(user, section)
        cards = build_report_cards(section, term)

        if request.method == "POST" and (may_teacher or may_principal):
            try:
                saved = 0
                for card in cards:
                    e = card.enrollment
                    t_key, p_key = f"teacher_{e.pk}", f"principal_{e.pk}"
                    ReportComment.write(
                        e, term, user,
                        teacher_comment=request.POST.get(t_key) if (may_teacher and t_key in request.POST) else None,
                        principal_comment=request.POST.get(p_key) if (may_principal and p_key in request.POST) else None,
                    )
                    saved += 1
            except DomainError as exc:
                messages.error(request, f"{card.student.full_name}: {exc}")
            else:
                self.message_user(request, "Comments saved.", level=messages.SUCCESS)
                return redirect(request.path)
        rows = []
        for card in cards:
            teacher_text, principal_text = card.teacher_comment, card.principal_comment
            if request.method == "POST":
                teacher_text = request.POST.get(f"teacher_{card.enrollment.pk}", teacher_text)
                principal_text = request.POST.get(f"principal_{card.enrollment.pk}", principal_text)
            rows.append({"card": card, "teacher": teacher_text, "principal": principal_text})
        context = {
            **self.admin_site.each_context(request),
            "title": f"Report card comments: {section.display_name}, {term.name}",
            "section": section, "term": term, "rows": rows, "max_comment": MAX_COMMENT,
            "may_teacher": may_teacher, "may_principal": may_principal, "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/reports/comments.html", context)

    # --- PDF -----------------------------------------------------------------
    def pdf_view(self, request, section_id, term_id, enrollment_id=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        section, term = self._section_and_term(request, section_id, term_id)
        only = {enrollment_id} if enrollment_id else None
        cards = build_report_cards(section, term, enrollment_ids=only)
        if enrollment_id and not cards:
            return HttpResponse(status=404)
        pdf = render_report_cards(cards)
        AuditLog.record(request.user, "report_cards_generated", section,
                        after={"term": term.name, "students": len(cards), "provisional": not term.is_locked})
        name = _filename("report-card", cards[0].student.full_name) if enrollment_id else _filename("report-cards", section.display_name, term.name)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{name}"'
        return response
