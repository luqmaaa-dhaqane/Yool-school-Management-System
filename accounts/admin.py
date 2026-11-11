from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from audit.mixins import AuditedAdminMixin
from audit.models import AuditLog
from core.admin_mixins import SchoolScopedAdminMixin
from core.exceptions import DomainError

from .models import User

# Fields nobody may change on their own account (no self-demotion, self-lockout or privilege games).
SELF_LOCKED_FIELDS = ("role", "school", "is_active", "is_staff", "is_superuser", "groups", "user_permissions")


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

    # --- protection ------------------------------------------------------
    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None:
            return True
        if obj.pk == request.user.pk:
            return False  # nobody can delete their own account
        if obj.is_superuser and not request.user.is_superuser:
            return False
        if obj.is_last_super_admin():
            return False
        return True

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.pk == request.user.pk:
            readonly += [f for f in SELF_LOCKED_FIELDS if f not in readonly]
        return readonly

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)  # accounts are deactivated, never bulk-deleted
        return actions

    @admin.action(description="Deactivate selected users", permissions=["change"])
    def deactivate_users(self, request, queryset):
        done = 0
        for user in queryset:
            if user.pk == request.user.pk:
                self.message_user(request, "You cannot deactivate your own account.", level=messages.ERROR)
                continue
            try:
                user.is_active = False
                user.save()
            except DomainError as exc:
                self.message_user(request, f"{user}: {exc}", level=messages.ERROR)
            else:
                AuditLog.record(request.user, "deactivate", user)
                done += 1
        if done:
            self.message_user(request, f"{done} user(s) deactivated.", level=messages.SUCCESS)

    actions = ["deactivate_users"]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # The admin form may overwrite groups; the role is the source of truth.
        form.instance.sync_role_group()
