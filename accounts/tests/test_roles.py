from django.test import TestCase

from accounts.models import Role, User
from accounts.permissions import sync_role_groups
from core.models import School


def make_user(role, school, username=None):
    return User.objects.create_user(
        username=username or role, password="x-long-test-password-1", role=role, school=school
    )


class RoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="S")
        sync_role_groups()

    def test_every_role_is_school_staff_with_admin_access(self):
        for role in Role:
            self.assertTrue(make_user(role, self.school, f"u-{role}").is_staff, role)

    def test_removed_roles_no_longer_exist(self):
        self.assertEqual(set(Role.values), {"super_admin", "principal", "registrar", "teacher"})

    def test_legacy_groups_are_deleted_on_sync(self):
        from django.contrib.auth.models import Group

        Group.objects.create(name="guardian")
        sync_role_groups()
        self.assertFalse(Group.objects.filter(name="guardian").exists())

    def test_super_admin_is_superuser(self):
        user = make_user(Role.SUPER_ADMIN, self.school)
        self.assertTrue(user.is_superuser and user.is_staff)

    def test_user_joins_exactly_one_role_group(self):
        user = make_user(Role.TEACHER, self.school)
        self.assertEqual(list(user.groups.values_list("name", flat=True)), ["teacher"])
        user.role = Role.REGISTRAR
        user.save()
        self.assertEqual(list(user.groups.values_list("name", flat=True)), ["registrar"])

    def test_accounts_with_removed_roles_are_deactivated_not_deleted(self):
        user = User.objects.create_user(username="old", password="x-long-test-password-1", school=self.school)
        User.objects.filter(pk=user.pk).update(role="accountant", is_staff=True)  # as left by Phase 0
        sync_role_groups()
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff)

    def test_blank_phones_do_not_clash(self):
        make_user(Role.TEACHER, self.school, "t1")
        make_user(Role.TEACHER, self.school, "t2")  # would fail on UNIQUE if '' were stored

    def test_createsuperuser_gets_super_admin_role(self):
        user = User.objects.create_superuser("root", password="x-long-test-password-1")
        self.assertEqual(user.role, Role.SUPER_ADMIN)


class PermissionMatrixTests(TestCase):
    """Automated version of the permission matrix in the design doc."""

    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="S")
        sync_role_groups()
        cls.users = {r: make_user(r, cls.school) for r in Role}

    def can(self, role, perm):
        # Reload to avoid cached permissions.
        return User.objects.get(pk=self.users[role].pk).has_perm(perm)

    def test_registrar_can_create_and_edit_students_but_not_delete(self):
        self.assertTrue(self.can(Role.REGISTRAR, "people.add_student"))
        self.assertTrue(self.can(Role.REGISTRAR, "people.change_student"))
        self.assertFalse(self.can(Role.REGISTRAR, "people.delete_student"))

    def test_teacher_is_read_only_on_students(self):
        self.assertTrue(self.can(Role.TEACHER, "people.view_student"))
        self.assertFalse(self.can(Role.TEACHER, "people.add_student"))
        self.assertFalse(self.can(Role.TEACHER, "people.change_student"))

    def test_medical_notes_restricted(self):
        self.assertTrue(self.can(Role.REGISTRAR, "people.view_medical_notes"))
        self.assertTrue(self.can(Role.PRINCIPAL, "people.view_medical_notes"))
        self.assertFalse(self.can(Role.TEACHER, "people.view_medical_notes"))

    def test_only_principal_can_view_audit_log(self):
        self.assertTrue(self.can(Role.PRINCIPAL, "audit.view_auditlog"))
        for role in (Role.REGISTRAR, Role.TEACHER):
            self.assertFalse(self.can(role, "audit.view_auditlog"), role)

    def test_enrollment_permissions(self):
        self.assertTrue(self.can(Role.REGISTRAR, "academics.add_enrollment"))
        self.assertTrue(self.can(Role.TEACHER, "academics.view_enrollment"))
        self.assertFalse(self.can(Role.TEACHER, "academics.add_enrollment"))
        self.assertFalse(self.can(Role.REGISTRAR, "academics.delete_enrollment"))

    def test_only_principal_manages_academic_setup(self):
        self.assertTrue(self.can(Role.PRINCIPAL, "academics.add_academicyear"))
        self.assertFalse(self.can(Role.REGISTRAR, "academics.add_academicyear"))
        self.assertFalse(self.can(Role.TEACHER, "academics.change_classsection"))


class AdminAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="S")
        cls.other = School.objects.create(name="Other")
        sync_role_groups()

    def test_registrar_reaches_student_list(self):
        user = make_user(Role.REGISTRAR, self.school)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/people/student/").status_code, 200)

    def test_teacher_cannot_add_student(self):
        user = make_user(Role.TEACHER, self.school)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/people/student/add/").status_code, 403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get("/admin/people/student/")
        self.assertEqual(response.status_code, 302)


class SyncRolesCommandTests(TestCase):
    def test_command_creates_groups_with_permissions(self):
        from django.contrib.auth.models import Group
        from django.core.management import call_command

        call_command("sync_roles", verbosity=0)
        self.assertTrue(Group.objects.get(name="teacher").permissions.filter(codename="add_attendanceregister").exists())
        call_command("sync_roles", verbosity=0)  # safe to repeat
        self.assertEqual(Group.objects.filter(name="teacher").count(), 1)
