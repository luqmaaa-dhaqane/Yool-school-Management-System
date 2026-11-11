from django.contrib.auth.models import AbstractUser, Group, UserManager as DjangoUserManager
from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from core.exceptions import DomainError


class Role(models.TextChoices):
    SUPER_ADMIN = "super_admin", "Super Admin"
    PRINCIPAL = "principal", "Principal / Admin"
    REGISTRAR = "registrar", "Registrar"
    TEACHER = "teacher", "Teacher"


# Roles that were removed when the scope was reduced (no parent portal, no fees).
LEGACY_ROLES = ("accountant", "guardian", "student")


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.SUPER_ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """A login account. The role drives permissions via a same-named Group."""

    school = models.ForeignKey(
        "core.School", null=True, blank=True, on_delete=models.PROTECT, related_name="users"
    )
    role = models.CharField(max_length=20, choices=Role.choices, blank=True)
    phone = models.CharField(max_length=40, null=True, blank=True, unique=True)
    language = models.CharField(max_length=5, default="en")

    objects = UserManager()

    def __str__(self):
        return self.get_full_name() or self.username

    # --- behaviour -------------------------------------------------------
    def has_role(self, *roles):
        return self.role in roles

    # --- which classes this user may work with ----------------------------
    def _school_sections(self):
        from academics.models import ClassSection

        qs = ClassSection.objects.all()
        if self.is_superuser:
            return qs
        if self.role not in Role.values or not self.school_id:
            return qs.none()
        return qs.filter(academic_year__school_id=self.school_id)

    def managed_sections(self):
        """Classes this user runs: attendance, class lists, mark sheets.

        Teachers: only the class where they are the homeroom teacher.
        Principal/Registrar: every class of their school. Super admin: everything.
        """
        qs = self._school_sections()
        if self.role == Role.TEACHER and not self.is_superuser:
            qs = qs.filter(homeroom_teacher=self)
        return qs

    def accessible_sections(self):
        """Classes this user may see: the ones they run plus the ones where they teach a subject."""
        qs = self._school_sections()
        if self.role == Role.TEACHER and not self.is_superuser:
            qs = qs.filter(models.Q(homeroom_teacher=self) | models.Q(class_subjects__teacher=self)).distinct()
        return qs

    def can_take_attendance_for(self, section):
        """Attendance is the class's homeroom teacher's job (or admin staff); subject teachers do not take it."""
        return self.managed_sections().filter(pk=section.pk).exists()

    # --- protecting the last super admin ----------------------------------
    @classmethod
    def active_super_admins(cls):
        return cls.objects.filter(is_active=True, is_superuser=True)

    def is_last_super_admin(self):
        return (
            self.pk is not None
            and self.is_superuser
            and self.is_active
            and not User.active_super_admins().exclude(pk=self.pk).exists()
        )

    def sync_role_group(self):
        """Make the user's group membership match their role (one role group)."""
        role_groups = Group.objects.filter(name__in=Role.values)
        self.groups.remove(*role_groups)
        if self.role:
            group = Group.objects.filter(name=self.role).first()
            if group:
                self.groups.add(group)

    def save(self, *args, **kwargs):
        self.phone = self.phone or None  # allow many blank phones with UNIQUE
        if self.role in Role.values:
            self.is_superuser = self.role == Role.SUPER_ADMIN  # the role is the single source of truth
            self.is_staff = True  # every remaining role is school staff
        if not self._state.adding:
            before = User.objects.filter(pk=self.pk).values("is_superuser", "is_active").first()
            if before and before["is_superuser"] and before["is_active"]:
                still_super = self.is_superuser and self.is_active
                if not still_super and not User.active_super_admins().exclude(pk=self.pk).exists():
                    raise DomainError(
                        "This is the only active super admin. Create another super admin before "
                        "demoting or deactivating this account."
                    )
        super().save(*args, **kwargs)
        self.sync_role_group()


@receiver(pre_delete, sender=User)
def _protect_last_super_admin(sender, instance, **kwargs):
    if instance.is_last_super_admin():
        raise DomainError("The last active super admin cannot be deleted.")
