"""What the home page shows, depending on who is looking."""
from datetime import date

from django.urls import reverse

from academics.models import ClassSubject, Enrollment
from academics.selectors import default_term
from attendance.services import chronic_absentees, class_day_rows
from grades.models import Assessment


def _running_on(qs, day):
    return (
        qs.filter(academic_year__starts_on__lte=day, academic_year__ends_on__gte=day)
        .select_related("grade_level", "academic_year", "homeroom_teacher")
        .order_by("grade_level__sort_order")
    )


def _plural(n, word, plural=None):
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def build_dashboard(user, today=None):
    """KPIs, today's registers, things needing attention and the user's subjects. None for non-staff."""
    if not (user.is_authenticated and user.is_staff):
        return None
    today = today or date.today()
    managed = list(_running_on(user.managed_sections(), today))
    accessible = _running_on(user.accessible_sections(), today)
    term = default_term(user)
    can_take = user.has_perm("attendance.add_attendanceregister") and user.has_perm("attendance.change_attendanceregister")

    rows = class_day_rows(managed, today) if managed else []
    for r in rows:
        r["take_url"] = (
            reverse("admin:attendance_take", args=[r["section"].pk, today.isoformat()]) if can_take and not r["problem"] else ""
        )
    taken = [r for r in rows if r["register"]]
    pending = [r for r in rows if not r["register"] and not r["problem"]]
    no_school = bool(rows) and all(r["problem"] for r in rows)

    n_classes = accessible.count()
    enrolled = Enrollment.objects.filter(status=Enrollment.Status.ENROLLED, class_section__in=accessible).count()
    kpis = [{"label": "Students enrolled", "value": enrolled, "sub": f"across {_plural(n_classes, 'class', 'classes')}"}]

    if managed:
        if taken:
            attended = sum(r["summary"].present + r["summary"].late for r in taken)
            recorded = sum(r["summary"].days for r in taken)
            kpis.append({"label": "Present today", "value": f"{attended} of {recorded}", "sub": f"from {_plural(len(taken), 'register')} taken"})
        else:
            kpis.append({"label": "Present today", "value": "-", "sub": "no school today" if no_school else "no register taken yet"})
        if no_school:
            kpis.append({"label": "Registers not taken", "value": "-", "sub": "no school today"})
        else:
            names = ", ".join(r["section"].display_name for r in pending[:3])
            kpis.append({"label": "Registers not taken", "value": len(pending), "sub": names or "all done"})
    else:
        subjects_count = ClassSubject.objects.filter(teacher=user, class_section__in=accessible).count()
        assessments = Assessment.objects.filter(class_subject__teacher=user, term=term).count() if term else 0
        kpis.append({"label": "Subjects you teach", "value": subjects_count, "sub": "in the current year"})
        kpis.append({"label": "Assessments this term", "value": assessments, "sub": term.name if term else "no term yet"})
    if term:
        kpis.append({"label": term.name, "value": "Locked" if term.is_locked else "Open", "sub": f"ends {term.ends_on:%d %B}"})
    else:
        kpis.append({"label": "Term", "value": "-", "sub": "no term yet"})

    attention = []
    for r in pending[:3]:
        attention.append({"text": f"{r['section'].display_name} register is not taken yet.", "link": "Take attendance", "url": r["take_url"]})
    if term and not term.is_locked and user.has_perm("academics.change_term") and 0 <= (term.ends_on - today).days <= 21:
        attention.append({"text": f"{term.name} ends on {term.ends_on:%d %B}. Lock it before printing final report cards.",
                          "link": "Go to terms", "url": reverse("admin:academics_term_changelist")})
    if term and managed and user.has_perm("attendance.view_attendanceregister"):
        low = chronic_absentees([r["section"] for r in rows], term.starts_on, today, 90, 5)
        if low:
            attention.append({"text": f"{_plural(len(low), 'student')} below 90% attendance.", "link": "View list",
                              "url": reverse("admin:attendance_report_chronic")})

    subjects = []
    if term:
        for cs in (ClassSubject.objects.filter(teacher=user, class_section__in=accessible)
                   .select_related("class_section__grade_level", "subject").order_by("class_section__grade_level__sort_order", "subject__name")):
            subjects.append({
                "cs": cs, "assessments": Assessment.objects.filter(class_subject=cs, term=term).count(),
                "url": reverse("admin:grades_grid", args=[cs.pk, term.pk]),
            })
    return {"today": today, "term": term, "kpis": kpis, "registers": rows, "show_registers": bool(managed),
            "attention": attention, "subjects": subjects, "can_take": can_take}
