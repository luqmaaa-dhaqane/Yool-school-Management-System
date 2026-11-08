from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: the audit trail cannot be edited or deleted from the UI."""

    list_display = ("at", "user", "action", "entity", "entity_id")
    list_filter = ("action", "entity")
    search_fields = ("entity_id", "user__username")
    readonly_fields = ("user", "action", "entity", "entity_id", "before", "after", "at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
