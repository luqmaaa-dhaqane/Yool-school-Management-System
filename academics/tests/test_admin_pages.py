import io
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from academics.models import AcademicYear, ClassSection, Enrollment, GradeLevel
from accounts.models import Role, User
from accounts.permissions import sync_role_groups
from audit.models import AuditLog
from core.models import School
from people.models import Student


class AdminPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="S")
        sync_role_groups()
        cls.y1 = AcademicYear.objects.create(
            school=cls.school, name="Y1", starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30), is_current=True)
        cls.y2 = AcademicYear.objects.create(
            school=cls.school, name="Y2", starts_on=date(2027, 9, 1), ends_on=date(2028, 6, 30))
        cls.g1 = GradeLevel.objects.create(school=cls.school, name="Grade 1", sort_order=1)
        cls.g2 = GradeLevel.objects.create(school=cls.school, name="Grade 2", sort_order=2)
        cls.sec = ClassSection.objects.create(academic_year=cls.y1, grade_level=cls.g1, name="A")
        cls.next = ClassSection.objects.create(academic_year=cls.y2, grade_level=cls.g2, name="A")
        cls.student = Student.objects.create(
            school=cls.school, first_name="Amina", last_name="Ali", gender="female", date_of_birth=date(2019, 1, 1))
        Enrollment.enroll(cls.student, cls.sec)

    def login(self, role):
        user = User.objects.create_user(username=role, password="x-long-test-password-1", role=role, school=self.school)
        self.client.force_login(user)
        return user

    def test_roster_page_and_csv(self):
        self.login(Role.TEACHER)
        page = self.client.get(f"/admin/academics/classsection/{self.sec.pk}/roster/")
        self.assertContains(page, "Amina Ali")
        csv_response = self.client.get(f"/admin/academics/classsection/{self.sec.pk}/roster/?format=csv")
        self.assertEqual(csv_response["Content-Type"], "text/csv")
        self.assertIn("Amina Ali", csv_response.content.decode())

    def test_teacher_cannot_open_promote_page(self):
        self.login(Role.TEACHER)
        response = self.client.get(f"/admin/academics/classsection/{self.sec.pk}/promote/")
        self.assertEqual(response.status_code, 403)

    def test_registrar_promotes_class_through_the_page(self):
        user = self.login(Role.REGISTRAR)
        url = f"/admin/academics/classsection/{self.sec.pk}/promote/"
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"target_section": self.next.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.student.current_class(), self.next)
        self.assertTrue(AuditLog.objects.filter(action="promote", user=user).exists())

    def test_withdraw_action_logs_and_updates(self):
        user = self.login(Role.REGISTRAR)
        enrollment = Enrollment.objects.get()
        response = self.client.post("/admin/academics/enrollment/", {
            "action": "withdraw_action", "_selected_action": [enrollment.pk]})
        self.assertEqual(response.status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, Enrollment.Status.WITHDRAWN)
        self.assertTrue(AuditLog.objects.filter(action="withdraw", user=user).exists())

    def test_import_page_preview_then_save(self):
        self.login(Role.REGISTRAR)
        csv_bytes = b"first_name,last_name,gender,date_of_birth\nNew,Kid,male,2018-02-02\n"
        url = "/admin/people/student/import/"
        self.assertContains(self.client.get(url), "Import students")
        preview = self.client.post(url, {"file": SimpleUploadedFile("s.csv", csv_bytes), "preview_only": "on"})
        self.assertContains(preview, "No errors")
        self.assertFalse(Student.objects.filter(first_name="New").exists())
        self.client.post(url, {"file": SimpleUploadedFile("s.csv", csv_bytes)})
        self.assertTrue(Student.objects.filter(first_name="New").exists())
        self.assertTrue(AuditLog.objects.filter(action="import").exists())

    def test_teacher_cannot_import(self):
        self.login(Role.TEACHER)
        self.assertEqual(self.client.get("/admin/people/student/import/").status_code, 403)

    def test_template_download(self):
        self.login(Role.REGISTRAR)
        response = self.client.get("/admin/people/student/import/template/")
        self.assertIn("first_name", response.content.decode())

    def test_student_list_shows_current_class(self):
        self.login(Role.REGISTRAR)
        self.assertContains(self.client.get("/admin/people/student/"), "Grade 1 A")
