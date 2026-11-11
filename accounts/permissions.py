"""Role -> permission matrix (section 3 of the design doc).

Row-level scoping (teacher -> own classes, guardian -> own children) is
enforced in later phases in services/queries; this file defines what each
role may do with each model.
"""
from django.contrib.auth.models import Group, Permission

from .models import LEGACY_ROLES, Role, User


def P(model, *actions):
    return [f"{a}_{model}" for a in actions]


VIEW = ("view",)
CRUD = ("view", "add", "change", "delete")
EDIT = ("view", "add", "change")

ROLE_PERMISSIONS = {
    Role.PRINCIPAL: {
        "people": P("student", *EDIT) + ["view_medical_notes"]
        + P("guardian", *EDIT) + P("studentguardian", *CRUD),
        "academics": (
            P("academicyear", *EDIT) + P("term", *CRUD) + P("gradelevel", *EDIT)
            + P("classsection", *CRUD) + P("subject", *EDIT) + P("enrollment", *EDIT)
            + P("classsubject", *CRUD)
        ),
        "grades": (
            P("assessmenttype", *EDIT) + P("gradeband", *EDIT) + P("assessment", *CRUD) + P("gradeentry", *EDIT)
        ),
        "core": P("school", "view", "change"),
        "audit": P("auditlog", *VIEW),
        "accounts": P("user", *VIEW),
        "attendance": P("attendanceregister", *EDIT) + P("attendancerecord", *VIEW) + P("holiday", *CRUD),
    },
    Role.REGISTRAR: {
        "people": P("student", *EDIT) + ["view_medical_notes"]
        + P("guardian", *EDIT) + P("studentguardian", *CRUD),
        "academics": (
            P("academicyear", *VIEW) + P("term", *VIEW) + P("gradelevel", *VIEW)
            + P("classsection", *VIEW) + P("subject", *VIEW) + P("enrollment", *EDIT)
            + P("classsubject", *EDIT)
        ),
        "grades": (
            P("assessmenttype", *VIEW) + P("gradeband", *VIEW) + P("assessment", *VIEW) + P("gradeentry", *VIEW)
        ),
        "attendance": P("attendanceregister", *EDIT) + P("attendancerecord", *VIEW) + P("holiday", *VIEW),
    },
    Role.TEACHER: {
        "people": P("student", *VIEW) + P("guardian", *VIEW),
        "academics": (
            P("academicyear", *VIEW) + P("term", *VIEW) + P("gradelevel", *VIEW)
            + P("classsection", *VIEW) + P("subject", *VIEW) + P("enrollment", *VIEW)
            + P("classsubject", *VIEW)
        ),
        "grades": (
            P("assessmenttype", *VIEW) + P("gradeband", *VIEW) + P("assessment", *CRUD) + P("gradeentry", *EDIT)
        ),
        "attendance": P("attendanceregister", *EDIT) + P("attendancerecord", *VIEW) + P("holiday", *VIEW),
    },
}


def sync_role_groups():
    """Create/update one Group per role with its permissions. Idempotent."""
    for role in Role:
        group, _ = Group.objects.get_or_create(name=role.value)
        spec = ROLE_PERMISSIONS.get(role, {})
        perms = []
        for app_label, codenames in spec.items():
            for codename in codenames:
                perms.append(
                    Permission.objects.get(
                        content_type__app_label=app_label, codename=codename
                    )
                )
        group.permissions.set(perms)
    Group.objects.filter(name__in=LEGACY_ROLES).delete()  # removed roles
    # Accounts that still hold a removed role can no longer be used; deactivate, never delete.
    User.objects.filter(role__in=LEGACY_ROLES).update(is_active=False, is_staff=False)
    for user in User.objects.exclude(role=""):
        user.sync_role_group()
