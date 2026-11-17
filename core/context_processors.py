from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import reverse
from django.utils.safestring import mark_safe


def branding(request):
    """School name, subtitle and logo for every template. The logo shows only if the file exists."""
    logo_url = static(settings.SITE_LOGO_STATIC) if finders.find(settings.SITE_LOGO_STATIC) else ""
    return {
        "brand_name": settings.SITE_NAME,
        "brand_subtitle": settings.SITE_SUBTITLE,
        "brand_short_name": settings.SITE_SHORT_NAME,
        "brand_logo_url": logo_url,
    }


# Sidebar: (label, url name, icon, permission needed, URL prefixes that mark it as the current section)
_ICONS = {
    "Overview": '<path d="M3 11l9-8 9 8M5 10v10h14V10"/>',
    "Students": '<circle cx="9" cy="8" r="3.5"/><path d="M2 20c0-4 3-6 7-6s7 2 7 6M16 4.5a3.5 3.5 0 010 7M18 14c3 .6 4 2.6 4 6"/>',
    "Classes": '<path d="M3 8l9-4 9 4-9 4zM7 10.5V15c2.5 2 7.5 2 10 0v-4.5"/>',
    "Attendance": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18M9 15l2 2 4-4"/>',
    "Gradebook": '<path d="M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2zM4 19V5M9 8h6"/>',
    "Report cards": '<path d="M6 3h9l4 4v14H6zM15 3v4h4M9 12h6M9 16h6"/>',
    "Settings": '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    "Help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 015 .5c0 1.5-2.5 2-2.5 3.5M12 17h.01"/>',
}
_NAV = [
    ("Overview", "admin:index", None, ("/admin/",)),
    ("Students", "admin:people_student_changelist", "people.view_student", ("/admin/people/",)),
    ("Classes", "admin:academics_classsection_changelist", "academics.view_classsection",
     ("/admin/academics/classsection/", "/admin/academics/enrollment/", "/admin/academics/classsubject/")),
    ("Attendance", "admin:attendance_attendanceregister_changelist", "attendance.view_attendanceregister",
     ("/admin/attendance/attendanceregister/", "/admin/attendance/attendancerecord/")),
    ("Gradebook", "admin:grades_assessment_changelist", "grades.view_assessment",
     ("/admin/grades/assessment/", "/admin/grades/gradeentry/")),
    ("Report cards", "admin:reports_reportcomment_changelist", "reports.view_reportcomment", ("/admin/reports/",)),
]


def navigation(request):
    """Sidebar items (only what the person may open), the current section, and the Settings tabs."""
    from .settings_registry import visible_sections

    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_staff):
        return {"nav_items": [], "settings_tabs": [], "settings_pills": []}
    path = request.path
    sections = visible_sections(user)
    settings_prefixes = ("/admin/settings/",) + tuple(i["prefix"] for sec in sections for i in sec["items"])
    in_settings = any(path.startswith(p) for p in settings_prefixes)

    items, current = [], None
    for label, url_name, perm, prefixes in _NAV:
        if perm and not user.has_perm(perm):
            continue
        item = {"label": label, "url": reverse(url_name), "icon": mark_safe(_ICONS[label]), "active": False}
        items.append(item)
        if label == "Overview":
            if path.rstrip("/") == "/admin":
                current = item
        elif any(path.startswith(p) for p in prefixes):
            current = item
    settings_item = None
    if user.is_superuser or user.has_perm("academics.add_academicyear"):
        settings_item = {"label": "Settings", "url": reverse("admin:settings"), "icon": mark_safe(_ICONS["Settings"]), "active": False}
        items.append(settings_item)
    help_item = {"label": "Help", "url": reverse("admin:help"), "icon": mark_safe(_ICONS["Help"]), "active": False}
    items.append(help_item)
    if path.startswith("/admin/help/"):
        current = help_item
    elif settings_item and (in_settings or (current is None and path.startswith("/admin/") and path.rstrip("/") != "/admin")):
        current = settings_item
    if current:
        current["active"] = True

    tabs, pills = [], []
    if in_settings and sections:
        hub = path.startswith("/admin/settings/")
        tabs.append({"label": "All settings", "url": reverse("admin:settings"), "active": hub})
        for sec in sections:
            owns = any(path.startswith(i["prefix"]) for i in sec["items"])
            tabs.append({"label": sec["label"], "url": sec["items"][0]["url"], "active": owns})
            if owns and len(sec["items"]) > 1:
                pills = [{"label": i["title"], "url": i["url"], "active": path.startswith(i["prefix"])} for i in sec["items"]]
    return {"nav_items": items, "settings_tabs": tabs, "settings_pills": pills}
