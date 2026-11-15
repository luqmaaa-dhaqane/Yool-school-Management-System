from django.contrib import admin


class YoolAdminSite(admin.AdminSite):
    """The same admin, with a role-aware dashboard on its home page."""

    def index(self, request, extra_context=None):
        from .dashboard import build_dashboard

        extra = dict(extra_context or {})
        extra["dashboard"] = build_dashboard(request.user)
        return super().index(request, extra)
