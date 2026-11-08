from django.contrib import admin

from audit.mixins import AuditedAdminMixin

from .models import School


@admin.register(School)
class SchoolAdmin(AuditedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "phone", "email", "currency")
