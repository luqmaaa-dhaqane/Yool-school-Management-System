import csv
from datetime import date, timedelta

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from academics.models import AcademicYear
from audit.mixins import AuditedAdminMixin
from core.admin_mixins import SchoolScopedAdminMixin
from core.csv_utils import safe_row
from core.exceptions import DomainError

from .forms import PeriodForm
from .models import AttendanceRecord, AttendanceRegister, Holiday, Status
from .services import chronic_absentees, class_day_rows, student_rows


def _parse_day(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def _sections_for_day(user, day):
    return (
        user.managed_sections()
        .filter(academic_year__starts_on__lte=day, academic_year__ends_on__gte=day)
        .select_related("grade_level", "academic_year", "homeroom_teacher")
        .order_by("grade_level__sort_order")
    )


def _default_period(user):
    """Start of the current (or latest started) term up to today."""
    today = date.today()
    year = AcademicYear.objects.filter(school_id=user.school_id, is_current=True).first() if user.school_id else None
    if year:
        term = year.term_on(today) or year.terms.filter(starts_on__lte=today).order_by("-starts_on").first()
        if term:
            return term.starts_on, today
    return today - timedelta(days=30), today


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(safe_row(row))
    return response


@admin.register(AttendanceRegister)
class AttendanceRegisterAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    """The 'Attendance' entry in the admin: a daily dashboard instead of a plain list."""

    school_lookup = "class_section__academic_year__school"

    def scope_for_teacher(self, qs, user):
        return qs.filter(class_section__homeroom_teacher=user)

    # registers are created and changed only through the Take attendance page
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("take/<uuid:section_id>/<str:day>/", wrap(self.take_view), name="attendance_take"),
            path("reports/daily/", wrap(self.daily_report), name="attendance_report_daily"),
            path("reports/students/", wrap(self.students_report), name="attendance_report_students"),
            path("reports/chronic/", wrap(self.chronic_report), name="attendance_report_chronic"),
        ]
        return custom + super().get_urls()

    # --- dashboard -------------------------------------------------------
    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        today = date.today()
        day = min(_parse_day(request.GET.get("date"), today), today)
        rows = class_day_rows(_sections_for_day(request.user, day), day)
        can_take = request.user.has_perm("attendance.add_attendanceregister") and request.user.has_perm(
            "attendance.change_attendanceregister"
        )
        context = {
            **self.admin_site.each_context(request),
            "title": "Attendance",
            "day": day,
            "prev_day": day - timedelta(days=1),
            "next_day": day + timedelta(days=1) if day < today else None,
            "today": today,
            "rows": rows,
            "can_take": can_take,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/attendance/dashboard.html", context)

    # --- take attendance -------------------------------------------------
    def take_view(self, request, section_id, day):
        user = request.user
        if not (user.has_perm("attendance.add_attendanceregister") and user.has_perm("attendance.change_attendanceregister")):
            raise PermissionDenied
        section = get_object_or_404(user.managed_sections().select_related("grade_level", "academic_year"), pk=section_id)
        try:
            day = date.fromisoformat(day)
        except ValueError:
            raise Http404
        problem = AttendanceRegister.day_problem(section, day, user)
        enrollments = section.enrollments_on(day)
        register = AttendanceRegister.objects.filter(class_section=section, date=day).first()
        existing = {str(r.enrollment_id): r for r in register.records.all()} if register else {}

        posted = request.POST if request.method == "POST" else None
        if posted is not None and not problem:
            marks = {
                str(e.pk): (posted.get(f"status_{e.pk}", Status.PRESENT), posted.get(f"reason_{e.pk}", ""))
                for e in enrollments
            }
            try:
                saved = AttendanceRegister.take(section, day, marks, user)
            except DomainError as exc:
                messages.error(request, str(exc))
            else:
                c = saved.counts()
                self.message_user(
                    request,
                    f"Attendance saved for {section}: {c['present']} present, {c['absent']} absent, "
                    f"{c['late']} late, {c['excused']} excused.",
                    level=messages.SUCCESS,
                )
                return redirect(f"{reverse('admin:attendance_attendanceregister_changelist')}?date={day.isoformat()}")

        rows = []
        for e in enrollments:
            rec = existing.get(str(e.pk))
            if posted is not None:
                status, reason = posted.get(f"status_{e.pk}", "present"), posted.get(f"reason_{e.pk}", "")
            else:
                status, reason = (rec.status, rec.reason) if rec else ("present", "")
            rows.append({"id": e.pk, "student": e.student, "status": status, "reason": reason})
        context = {
            **self.admin_site.each_context(request),
            "title": f"Attendance: {section}",
            "section": section,
            "day": day,
            "rows": rows,
            "problem": problem,
            "editing": register is not None,
            "statuses": Status.choices,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/attendance/take.html", context)

    # --- reports -----------------------------------------------------------
    def _check_report_access(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied

    def _render_report(self, request, title, form, header, rows, csv_name, date_picker=None):
        if request.GET.get("format") == "csv" and rows is not None:
            return _csv_response(csv_name, header, rows)
        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "form": form,
            "header": header,
            "rows": rows,
            "date_picker": date_picker,
            "opts": self.model._meta,
            "query": request.GET.urlencode(),
        }
        return TemplateResponse(request, "admin/attendance/report.html", context)

    def daily_report(self, request):
        self._check_report_access(request)
        today = date.today()
        day = min(_parse_day(request.GET.get("date"), today), today)
        data = class_day_rows(_sections_for_day(request.user, day), day)
        header = ["Class", "Students", "Register", "Present", "Absent", "Late", "Excused"]
        rows = []
        for r in data:
            s = r["summary"]
            status = r["problem"] or ("Taken" if r["register"] else "NOT TAKEN")
            rows.append([
                str(r["section"]), r["students"], status,
                s.present if s else "", s.absent if s else "", s.late if s else "", s.excused if s else "",
            ])
        return self._render_report(request, f"Daily attendance summary: {day:%A %d %b %Y}", None, header, rows,
                                   f"attendance-{day.isoformat()}.csv", date_picker={"day": day, "max": today})

    def students_report(self, request):
        self._check_report_access(request)
        sections = _sections_for_day(request.user, date.today())
        start, end = _default_period(request.user)
        bound = request.GET or {"start": start.isoformat(), "end": end.isoformat()}
        form = PeriodForm(bound, sections=sections, with_section=True)
        header = ["Student no", "Student", "Present", "Late", "Excused", "Absent", "Days recorded", "Attendance %"]
        rows = None
        if form.is_valid() and form.cleaned_data.get("section"):
            section = form.cleaned_data["section"]
            data = student_rows(section, form.cleaned_data.get("start"), form.cleaned_data.get("end"))
            rows = [[
                r["student"].student_no, r["student"].full_name, r["summary"].present, r["summary"].late,
                r["summary"].excused, r["summary"].absent, r["summary"].days,
                "" if r["summary"].rate is None else r["summary"].rate,
            ] for r in data]
            name = f"attendance-{section.display_name}.csv".replace(" ", "-")
        else:
            name = "attendance.csv"
        return self._render_report(request, "Attendance by student", form, header, rows, name)

    def chronic_report(self, request):
        self._check_report_access(request)
        start, end = _default_period(request.user)
        bound = request.GET.copy()
        for key, value in (("start", start.isoformat()), ("end", end.isoformat()), ("threshold", "90"), ("min_days", "5")):
            bound.setdefault(key, value)
        sections = _sections_for_day(request.user, date.today())
        form = PeriodForm(bound, with_threshold=True)
        header = ["Student no", "Student", "Class", "Attendance %", "Absent", "Late", "Excused", "Days recorded", "Guardian", "Guardian phone"]
        rows = []
        if form.is_valid():
            cd = form.cleaned_data
            data = chronic_absentees(sections, cd.get("start"), cd.get("end"), cd.get("threshold") or 90, cd.get("min_days") or 5)
            for r in data:
                s, g = r["summary"], r["guardian"]
                rows.append([
                    r["student"].student_no, r["student"].full_name, str(r["section"]), s.rate, s.absent, s.late,
                    s.excused, s.days, g.full_name if g else "", g.phone if g else "",
                ])
        return self._render_report(request, "Students with low attendance", form, header, rows, "low-attendance.csv")


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    """Read-only browser of individual marks (for checking and support)."""

    school_lookup = "register__class_section__academic_year__school"
    list_display = ("register", "enrollment", "status", "reason")
    list_filter = ("status", "register__date")
    search_fields = ("enrollment__student__first_name", "enrollment__student__last_name", "enrollment__student__student_no")
    list_select_related = ("register__class_section__grade_level", "enrollment__student")

    def scope_for_teacher(self, qs, user):
        return qs.filter(register__class_section__homeroom_teacher=user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Holiday)
class HolidayAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("date", "name")
    ordering = ("date",)
