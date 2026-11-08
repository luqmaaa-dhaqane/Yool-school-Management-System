import json

from django.core.serializers.json import DjangoJSONEncoder

from .models import AuditLog

_SENSITIVE = ("password", "token", "secret")


def _plain(value):
    try:
        json.dumps(value, cls=DjangoJSONEncoder)
        return value
    except TypeError:
        return str(value)


def _clean(mapping, keys):
    return {
        k: _plain(mapping.get(k))
        for k in keys
        if not any(s in k.lower() for s in _SENSITIVE)
    }


class AuditedAdminMixin:
    """Writes an AuditLog row for every create, update and delete in the admin."""

    def save_model(self, request, obj, form, change):
        before = _clean(form.initial, form.changed_data) if change else None
        super().save_model(request, obj, form, change)
        after = _clean(form.cleaned_data, form.changed_data)
        AuditLog.record(request.user, "update" if change else "create", obj, before, after)

    def delete_model(self, request, obj):
        AuditLog.record(request.user, "delete", obj, before={"repr": str(obj)})
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            AuditLog.record(request.user, "delete", obj, before={"repr": str(obj)})
        super().delete_queryset(request, queryset)
