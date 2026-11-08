from django.contrib.auth.models import AbstractUser, Group, UserManager as DjangoUserManager
from django.db import models


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
        if self.role == Role.SUPER_ADMIN:
            self.is_superuser = True
            self.is_staff = True
        elif self.role in Role.values:
            self.is_staff = True  # every remaining role is school staff
        super().save(*args, **kwargs)
        self.sync_role_group()
