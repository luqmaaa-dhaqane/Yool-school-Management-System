from django.db import transaction
from django.test import TestCase, override_settings

from accounts.models import Role, User
from accounts.permissions import sync_role_groups
from audit.models import AuditLog
from core.exceptions import DomainError
from core.models import School

PW = "x-long-test-password-1"


class SuperAdminProtectionTests(TestCase):
    def setUp(self):
        sync_role_groups()
        self.school = School.objects.create(name="S")
        self.boss = User.objects.create_user("boss", password=PW, role=Role.SUPER_ADMIN, school=self.school)

    def second(self):
        return User.objects.create_user("boss2", password=PW, role=Role.SUPER_ADMIN, school=self.school)

    # --- the last super admin ---------------------------------------------
    def test_last_super_admin_cannot_be_deleted(self):
        with self.assertRaisesMessage(DomainError, "last active super admin"), transaction.atomic():
            self.boss.delete()
        self.assertTrue(User.objects.filter(pk=self.boss.pk).exists())

    def test_queryset_delete_is_also_blocked(self):
        with self.assertRaises(DomainError), transaction.atomic():
            User.objects.filter(pk=self.boss.pk).delete()

    def test_last_super_admin_cannot_be_deactivated_or_demoted(self):
        self.boss.is_active = False
        with self.assertRaisesMessage(DomainError, "only active super admin"):
            self.boss.save()
        boss = User.objects.get(pk=self.boss.pk)
        boss.role = Role.PRINCIPAL
        with self.assertRaises(DomainError):
            boss.save()
        self.assertEqual(User.objects.get(pk=self.boss.pk).role, Role.SUPER_ADMIN)

    def test_with_a_second_super_admin_changes_are_allowed(self):
        other = self.second()
        other.is_active = False
        other.save()
        other.is_active = True
        other.save()
        other.delete()
        self.assertFalse(User.objects.filter(username="boss2").exists())

    def test_an_inactive_second_super_admin_does_not_count(self):
        other = self.second()
        User.objects.filter(pk=other.pk).update(is_active=False)
        with self.assertRaises(DomainError), transaction.atomic():
            self.boss.delete()

    # --- role and superuser flag stay in step -------------------------------
    def test_superuser_flag_follows_the_role(self):
        self.second()
        user = User.objects.create_user("p", password=PW, role=Role.PRINCIPAL, school=self.school)
        self.assertFalse(user.is_superuser)
        user.role = Role.SUPER_ADMIN
        user.save()
        self.assertTrue(user.is_superuser)
        user.role = Role.TEACHER
        user.save()
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)  # demotion removes the power too

    def test_createsuperuser_command_path_still_works(self):
        user = User.objects.create_superuser("root", password=PW)
        self.assertEqual((user.role, user.is_superuser), (Role.SUPER_ADMIN, True))


class AdminProtectionTests(TestCase):
    def setUp(self):
        sync_role_groups()
        self.school = School.objects.create(name="S")
        mk = lambda name, role: User.objects.create_user(name, password=PW, role=role, school=self.school)
        self.boss, self.boss2 = mk("boss", Role.SUPER_ADMIN), mk("boss2", Role.SUPER_ADMIN)
        self.teacher, self.principal = mk("teach", Role.TEACHER), mk("prin", Role.PRINCIPAL)
        self.client.force_login(self.boss)

    def test_super_admin_cannot_delete_themselves_even_with_another_super_admin(self):
        url = f"/admin/accounts/user/{self.boss.pk}/delete/"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.boss.pk).exists())

    def test_no_delete_button_on_own_account_page(self):
        page = self.client.get(f"/admin/accounts/user/{self.boss.pk}/change/").content.decode()
        self.assertNotIn("deletelink", page)

    def test_super_admin_can_delete_another_user(self):
        response = self.client.post(f"/admin/accounts/user/{self.teacher.pk}/delete/", {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.teacher.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete", entity="accounts.User").exists())

    def test_bulk_delete_is_not_offered(self):
        page = self.client.get("/admin/accounts/user/").content.decode()
        self.assertNotIn("delete_selected", page)
        self.assertIn("deactivate_users", page)

    def test_bulk_delete_post_does_nothing(self):
        self.client.post("/admin/accounts/user/", {"action": "delete_selected", "_selected_action": [self.teacher.pk], "post": "yes"})
        self.assertTrue(User.objects.filter(pk=self.teacher.pk).exists())

    def test_deactivate_action_skips_yourself(self):
        self.client.post("/admin/accounts/user/", {"action": "deactivate_users", "_selected_action": [self.boss.pk, self.teacher.pk]})
        self.assertTrue(User.objects.get(pk=self.boss.pk).is_active)
        self.assertFalse(User.objects.get(pk=self.teacher.pk).is_active)

    def test_own_role_and_status_fields_are_read_only(self):
        page = self.client.get(f"/admin/accounts/user/{self.boss.pk}/change/").content.decode()
        self.assertNotIn('name="role"', page)
        self.assertNotIn('name="is_active"', page)
        self.assertNotIn('name="is_superuser"', page)
        # another user's page still has them
        other = self.client.get(f"/admin/accounts/user/{self.teacher.pk}/change/").content.decode()
        self.assertIn('name="role"', other)

    def test_tampered_post_cannot_demote_yourself(self):
        self.client.post(f"/admin/accounts/user/{self.boss.pk}/change/", {
            "username": "boss", "role": Role.TEACHER, "is_active": "", "first_name": "", "last_name": "", "email": "",
            "date_joined_0": "2026-01-01", "date_joined_1": "00:00:00", "language": "en"})
        boss = User.objects.get(pk=self.boss.pk)
        self.assertEqual((boss.role, boss.is_active, boss.is_superuser), (Role.SUPER_ADMIN, True, True))

    def test_principal_cannot_delete_or_edit_users(self):
        self.client.force_login(self.principal)
        self.assertEqual(self.client.post(f"/admin/accounts/user/{self.teacher.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.teacher.pk).exists())
        self.assertEqual(self.client.get("/admin/accounts/user/add/").status_code, 403)

    def test_principal_cannot_delete_a_super_admin(self):
        self.client.force_login(self.principal)
        self.assertEqual(self.client.post(f"/admin/accounts/user/{self.boss2.pk}/delete/", {"post": "yes"}).status_code, 403)


class LoginLockoutTests(TestCase):
    def setUp(self):
        sync_role_groups()
        self.user = User.objects.create_user("teach", password=PW, role=Role.TEACHER, school=School.objects.create(name="S"))

    def attempt(self, password):
        return self.client.post("/admin/login/", {"username": "teach", "password": password, "next": "/admin/"}, REMOTE_ADDR="10.0.0.9")

    @override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
    def test_locks_after_too_many_wrong_passwords(self):
        self.assertEqual(self.attempt("wrong").status_code, 200)
        self.assertEqual(self.attempt("wrong").status_code, 200)
        self.assertEqual(self.attempt("wrong").status_code, 429)  # the 3rd failure triggers the lockout
        self.assertEqual(self.attempt(PW).status_code, 429)       # even the right password is refused meanwhile

    @override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
    def test_correct_login_works_and_resets_the_counter(self):
        self.attempt("wrong")
        self.attempt("wrong")
        self.assertEqual(self.attempt(PW).status_code, 302)
        self.client.logout()
        for _ in range(2):
            self.attempt("wrong")
        self.assertEqual(self.attempt(PW).status_code, 302)  # counter was reset, so two more failures are fine

    @override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
    def test_other_users_are_not_locked_by_one_users_failures(self):
        other = User.objects.create_user("other", password=PW, role=Role.TEACHER, school=self.user.school)
        for _ in range(3):
            self.attempt("wrong")
        response = self.client.post("/admin/login/", {"username": "other", "password": PW, "next": "/admin/"}, REMOTE_ADDR="10.0.0.9")
        self.assertEqual(response.status_code, 302)
