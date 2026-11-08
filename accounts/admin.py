from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from audit.mixins import AuditedAdminMixin
from core.admin_mixins import SchoolScopedAdminMixin

from .models import User


@admin.register(User)
class UserAdmin(AuditedAdminMixin, SchoolScopedAdminMixin, DjangoUserAdmin):
    list_display = ("username", "get_full_name", "role", "school", "is_active")
    list_filter = ("role", "is_active", "school")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("School and role", {"fields": ("school", "role", "phone", "language")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("School and role", {"fields": ("school", "role", "first_name", "last_name", "email", "phone")}),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # The admin form may overwrite groups; the role is the source of truth.
        form.instance.sync_role_group()
