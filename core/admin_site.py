from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path


class YoolAdminSite(admin.AdminSite):
    """The same admin, with a role-aware dashboard, a Settings section and a Help page."""

    def get_urls(self):
        custom = [
            path("settings/", self.admin_view(self.settings_view), name="settings"),
            path("help/", self.admin_view(self.help_view), name="help"),
        ]
        return custom + super().get_urls()

    def index(self, request, extra_context=None):
        from .dashboard import build_dashboard

        extra = dict(extra_context or {})
        extra["dashboard"] = build_dashboard(request.user)
        return super().index(request, extra)

    def settings_view(self, request):
        from accounts.models import Role, User

        from .settings_registry import visible_sections

        sections = visible_sections(request.user)
        role_counts = []
        if request.user.school_id or request.user.is_superuser:
            qs = User.objects.filter(is_active=True)
            if not request.user.is_superuser:
                qs = qs.filter(school_id=request.user.school_id)
            for role in Role:
                n = qs.filter(role=role.value).count()
                if n:
                    role_counts.append(f"{n} {role.label.lower()}{'' if n == 1 else 's'}")
        context = {**self.each_context(request), "title": "Settings", "sections": sections, "role_counts": role_counts}
        return TemplateResponse(request, "admin/settings_hub.html", context)

    def help_view(self, request):
        context = {**self.each_context(request), "title": "Help"}
        return TemplateResponse(request, "admin/help.html", context)
