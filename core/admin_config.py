from django.contrib.admin.apps import AdminConfig


class YoolAdminConfig(AdminConfig):
    """django.contrib.admin, but using our admin site (role-aware dashboard on the home page)."""

    default_site = "core.admin_site.YoolAdminSite"
