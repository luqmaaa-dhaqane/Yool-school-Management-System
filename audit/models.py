from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class AuditLog(models.Model):
    """Append-only record of who changed what and when."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=20)
    entity = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=64)
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["entity", "entity_id"])]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.entity} {self.entity_id}"

    @classmethod
    def record(cls, user, action, obj, before=None, after=None):
        return cls.objects.create(
            user=user if getattr(user, "pk", None) else None,
            action=action,
            entity=obj._meta.label,
            entity_id=str(obj.pk),
            before=before,
            after=after,
        )
