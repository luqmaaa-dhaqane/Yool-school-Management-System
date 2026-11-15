from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from academics.models import AcademicYear, ClassSection, ClassSubject
from accounts.models import Role, User
from grades.models import Assessment, AssessmentType, GradeBand
from core.models import School


class LoginPageTests(TestCase):
    def test_login_page_shows_school_name_subtitle_and_show_password_script(self):
        page = self.client.get("/admin/login/").content.decode()
        self.assertIn("Yool International School", page)
        self.assertIn("Student Management System", page)
        self.assertIn("core/show_password.js", page)

    def test_tab_title_uses_the_short_name(self):
        self.assertIn("Yool SMS", self.client.get("/admin/login/").content.decode())

    @override_settings(SITE_NAME="Another School", SITE_SUBTITLE="Records")
    def test_name_is_configurable(self):
        page = self.client.get("/admin/login/").content.decode()
        self.assertIn("Another School", page)

    @override_settings(SITE_LOGO_STATIC="core/does-not-exist.jpg")
    def test_no_logo_image_when_file_is_missing(self):
        self.assertNotIn("<img", self.client.get("/admin/login/").content.decode())

    def test_the_school_logo_is_installed_and_shown(self):
        from django.contrib.staticfiles import finders
        from PIL import Image

        path = finders.find("core/logo.jpg")
        self.assertIsNotNone(path)
        Image.open(path).verify()  # a real, readable image
        self.assertIn("core/logo.jpg", self.client.get("/admin/login/").content.decode())

    def test_show_password_script_is_served_and_covers_password_fields(self):
        from django.contrib.staticfiles import finders

        path = finders.find("core/show_password.js")
        self.assertIsNotNone(path)
        source = open(path, encoding="utf-8").read()
        self.assertIn('input[type="password"]', source)
        self.assertIn("Show", source)

    def test_script_is_on_the_password_change_page_too(self):
        user = User.objects.create_user("t", password="x-long-test-password-1", role=Role.TEACHER, school=School.objects.create(name="S"))
        self.client.force_login(user)
        self.assertIn("core/show_password.js", self.client.get("/admin/password_change/").content.decode())

    def test_admin_header_shows_school_name(self):
        user = User.objects.create_user("t", password="x-long-test-password-1", role=Role.TEACHER, school=School.objects.create(name="S"))
        self.client.force_login(user)
        page = self.client.get("/admin/").content.decode()
        self.assertIn("Yool International School", page)
        self.assertIn("Student Management System", page)


class SeedTests(TestCase):
    def run_seed(self, **kw):
        out = StringIO()
        call_command("seed", school="Test School", admin_password="Seed-Passw0rd-123", stdout=out, **kw)
        return out.getvalue()

    def test_seed_sets_up_grading_and_subjects(self):
        self.run_seed()
        school = School.objects.get(name="Test School")
        self.assertEqual(sum(AssessmentType.objects.filter(school=school).values_list("weight_percent", flat=True)), 100)
        self.assertEqual(GradeBand.objects.filter(school=school).count(), 6)
        section = ClassSection.objects.filter(academic_year__school=school).first()
        self.assertEqual(section.class_subjects.count(), 10)

    def test_seed_is_safe_to_repeat(self):
        self.run_seed()
        counts = (ClassSubject.objects.count(), AssessmentType.objects.count(), GradeBand.objects.count(), User.objects.count())
        self.run_seed()
        self.assertEqual(counts, (ClassSubject.objects.count(), AssessmentType.objects.count(), GradeBand.objects.count(), User.objects.count()))

    def test_demo_creates_teacher_with_subjects_and_grades(self):
        out = self.run_seed(demo=True)
        self.assertIn("teaches Mathematics and English", out)
        teacher = User.objects.get(username="teacher")
        taught = set(ClassSubject.objects.filter(teacher=teacher).values_list("subject__code", flat=True))
        self.assertEqual(taught, {"MTH", "ENG"})
        self.assertEqual(Assessment.objects.count(), 3)
        self.assertTrue(teacher.accessible_sections().exists())


class FinishUpdateTests(TestCase):
    def test_adds_missing_setup_for_an_existing_school_and_keeps_edits(self):
        from datetime import date

        from academics.models import GradeLevel, Subject

        school = School.objects.create(name="Old School")
        year = AcademicYear.objects.create(school=school, name="Y", starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30), is_current=True)
        grade = GradeLevel.objects.create(school=school, name="Grade 1", sort_order=1)
        ClassSection.objects.create(academic_year=year, grade_level=grade)
        Subject.objects.create(school=school, code="MTH", name="Mathematics")
        Subject.objects.create(school=school, code="ENG", name="English")
        call_command("finish_update", stdout=StringIO())
        self.assertEqual(sum(AssessmentType.objects.values_list("weight_percent", flat=True)), 100)
        self.assertEqual(GradeBand.objects.count(), 6)
        self.assertEqual(ClassSubject.objects.count(), 2)
        AssessmentType.objects.filter(name="Exam").update(weight_percent=40)  # the school's own change
        call_command("finish_update", stdout=StringIO())
        self.assertEqual(AssessmentType.objects.get(name="Exam").weight_percent, 40)
        self.assertEqual(ClassSubject.objects.count(), 2)
        self.assertEqual(GradeBand.objects.count(), 6)
