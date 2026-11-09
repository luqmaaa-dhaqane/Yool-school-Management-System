from datetime import date, timedelta
from unittest import mock

from django.test import TestCase

from academics.models import AcademicYear, ClassSection, Enrollment, GradeLevel, Term
from accounts.models import Role, User
from accounts.permissions import sync_role_groups
from attendance.models import AttendanceRegister, Holiday
from core.csv_utils import safe_cell
from core.models import School
from people.models import Guardian, Student

TODAY = date.today()


def last_school_day(offset=0):
    """A recent weekday that is not Friday (the default non-teaching day)."""
    d, found = TODAY, -1
    while True:
        if d.weekday() != 4:
            found += 1
            if found == offset:
                return d
        d -= timedelta(days=1)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        sync_role_groups()
        cls.school = School.objects.create(name="S")
        cls.year = AcademicYear.objects.create(
            school=cls.school, name="Y", starts_on=TODAY - timedelta(days=60), ends_on=TODAY + timedelta(days=200), is_current=True)
        cls.term = Term.objects.create(academic_year=cls.year, name="T1", starts_on=TODAY - timedelta(days=60), ends_on=TODAY + timedelta(days=60))
        grade = GradeLevel.objects.create(school=cls.school, name="Grade 3", sort_order=3)
        mk = lambda name, role: User.objects.create_user(name, password="x-long-test-password-1", role=role, school=cls.school)
        cls.teacher, cls.other_teacher = mk("t1", Role.TEACHER), mk("t2", Role.TEACHER)
        cls.registrar, cls.principal = mk("r1", Role.REGISTRAR), mk("p1", Role.PRINCIPAL)
        cls.sec_a = ClassSection.objects.create(academic_year=cls.year, grade_level=grade, name="A", homeroom_teacher=cls.teacher)
        cls.sec_b = ClassSection.objects.create(academic_year=cls.year, grade_level=grade, name="B", homeroom_teacher=cls.other_teacher)
        cls.mine, cls.theirs = [], []
        guardian = Guardian.objects.create(school=cls.school, first_name="Mum", last_name="A", phone="0630000999")
        for name, sec, bucket in [("Abdi", cls.sec_a, cls.mine), ("Bilal", cls.sec_a, cls.mine), ("Zaid", cls.sec_b, cls.theirs)]:
            st = Student.objects.create(school=cls.school, first_name=name, last_name="T", gender="male", date_of_birth=date(2017, 1, 1))
            bucket.append(Enrollment.enroll(st, sec, on=cls.year.starts_on))
        cls.mine[0].student.add_guardian(guardian, "Mother")
        Guardian.objects.create(school=cls.school, first_name="Other", last_name="Parent", phone="0631111999")

    def login(self, user):
        self.client.force_login(user)

    def take_url(self, section, day):
        return f"/admin/attendance/attendanceregister/take/{section.pk}/{day.isoformat()}/"


class TeacherScopingTests(Base):
    def test_teacher_sees_only_own_students(self):
        self.login(self.teacher)
        page = self.client.get("/admin/people/student/").content.decode()
        self.assertIn("Abdi", page)
        self.assertNotIn("Zaid", page)

    def test_teacher_cannot_open_other_students_page(self):
        self.login(self.teacher)
        url = f"/admin/people/student/{self.theirs[0].student.pk}/change/"
        self.assertEqual(self.client.get(url).status_code, 302)  # admin treats it as "does not exist"

    def test_teacher_sees_only_own_class_and_enrollments_and_guardians(self):
        self.login(self.teacher)
        sections = self.client.get("/admin/academics/classsection/").content.decode()
        self.assertIn("Grade 3 A", sections)
        self.assertNotIn("Grade 3 B", sections)
        enrollments = self.client.get("/admin/academics/enrollment/").content.decode()
        self.assertIn("Abdi", enrollments)
        self.assertNotIn("Zaid", enrollments)
        guardians = self.client.get("/admin/people/guardian/").content.decode()
        self.assertIn("Mum", guardians)
        self.assertNotIn("Other Parent", guardians)

    def test_teacher_cannot_open_other_roster(self):
        self.login(self.teacher)
        self.assertEqual(self.client.get(f"/admin/academics/classsection/{self.sec_b.pk}/roster/").status_code, 404)

    def test_registrar_sees_everyone(self):
        self.login(self.registrar)
        page = self.client.get("/admin/people/student/").content.decode()
        self.assertIn("Abdi", page)
        self.assertIn("Zaid", page)


class DashboardAndTakeTests(Base):
    def test_dashboard_lists_only_own_class_for_teacher(self):
        self.login(self.teacher)
        page = self.client.get("/admin/attendance/attendanceregister/").content.decode()
        self.assertIn("Grade 3 A", page)
        self.assertNotIn("Grade 3 B", page)

    def test_registrar_dashboard_lists_all_classes(self):
        self.login(self.registrar)
        page = self.client.get("/admin/attendance/attendanceregister/").content.decode()
        self.assertIn("Grade 3 A", page)
        self.assertIn("Grade 3 B", page)

    def test_take_page_defaults_to_present(self):
        self.login(self.teacher)
        day = last_school_day()
        page = self.client.get(self.take_url(self.sec_a, day)).content.decode()
        self.assertIn("Abdi", page)
        self.assertNotIn("Zaid", page)
        self.assertEqual(page.count('value="present" checked'), 2)

    def test_post_saves_register(self):
        self.login(self.teacher)
        day = last_school_day()
        response = self.client.post(self.take_url(self.sec_a, day), {
            f"status_{self.mine[0].pk}": "absent", f"reason_{self.mine[0].pk}": "Fever",
            f"status_{self.mine[1].pk}": "present",
        })
        self.assertEqual(response.status_code, 302)
        register = AttendanceRegister.objects.get(class_section=self.sec_a, date=day)
        self.assertEqual(register.counts(), {"present": 1, "absent": 1, "late": 0, "excused": 0})
        self.assertEqual(register.records.get(enrollment=self.mine[0]).reason, "Fever")

    def test_edit_shows_saved_values(self):
        self.login(self.teacher)
        day = last_school_day()
        self.client.post(self.take_url(self.sec_a, day), {f"status_{self.mine[0].pk}": "late", f"reason_{self.mine[0].pk}": "Bus"})
        page = self.client.get(self.take_url(self.sec_a, day)).content.decode()
        self.assertIn("editing a saved register", page)
        self.assertIn('value="Bus"', page)

    def test_teacher_cannot_take_other_class(self):
        self.login(self.teacher)
        self.assertEqual(self.client.get(self.take_url(self.sec_b, last_school_day())).status_code, 404)

    def test_holiday_shows_message_and_blocks_post(self):
        day = last_school_day()
        Holiday.objects.create(school=self.school, date=day, name="Eid")
        self.login(self.teacher)
        page = self.client.get(self.take_url(self.sec_a, day)).content.decode()
        self.assertIn("Eid", page)
        self.client.post(self.take_url(self.sec_a, day), {})
        self.assertFalse(AttendanceRegister.objects.filter(date=day).exists())

    def test_future_date_blocked(self):
        self.login(self.registrar)
        page = self.client.get(self.take_url(self.sec_a, TODAY + timedelta(days=3))).content.decode()
        self.assertIn("future", page)

    def test_bad_date_is_404(self):
        self.login(self.registrar)
        self.assertEqual(self.client.get(f"/admin/attendance/attendanceregister/take/{self.sec_a.pk}/not-a-date/").status_code, 404)

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get("/admin/attendance/attendanceregister/").status_code, 302)


class ReportPageTests(Base):
    def setUp(self):
        day = last_school_day()
        AttendanceRegister.take(self.sec_a, day, {self.mine[0].pk: ("absent", "x")}, self.registrar)
        self.day = day

    def test_daily_report(self):
        self.login(self.registrar)
        page = self.client.get(f"/admin/attendance/attendanceregister/reports/daily/?date={self.day.isoformat()}").content.decode()
        self.assertIn("Grade 3 A", page)
        self.assertIn("NOT TAKEN", page)  # class B has no register

    def test_daily_report_csv(self):
        self.login(self.registrar)
        response = self.client.get(f"/admin/attendance/attendanceregister/reports/daily/?date={self.day.isoformat()}&format=csv")
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("Grade 3 A", response.content.decode())

    def test_student_report_needs_class_then_lists_students(self):
        self.login(self.teacher)
        url = "/admin/attendance/attendanceregister/reports/students/"
        self.assertIn("Choose a class", self.client.get(url).content.decode())
        page = self.client.get(f"{url}?section={self.sec_a.pk}").content.decode()
        self.assertIn("Abdi", page)
        self.assertIn("<td>0.0</td>", page)    # Abdi: absent on the one recorded day
        self.assertIn("<td>100.0</td>", page)  # Bilal: present on it
        self.assertNotIn("Zaid", page)

    def test_teacher_cannot_report_on_other_class(self):
        self.login(self.teacher)
        page = self.client.get(f"/admin/attendance/attendanceregister/reports/students/?section={self.sec_b.pk}").content.decode()
        self.assertNotIn("Zaid", page)

    def test_low_attendance_report_includes_guardian_phone(self):
        for offset in range(1, 6):
            d = last_school_day(offset)
            AttendanceRegister.take(self.sec_a, d, {self.mine[0].pk: ("absent", "")}, self.registrar)
        self.login(self.registrar)
        response = self.client.get("/admin/attendance/attendanceregister/reports/chronic/?threshold=90&min_days=5&format=csv")
        text = response.content.decode()
        self.assertIn("Abdi", text)
        self.assertIn("0630000999", text)
        self.assertNotIn("Bilal", text)

    def test_registers_are_read_only_in_admin(self):
        self.login(self.principal)
        self.assertEqual(self.client.get("/admin/attendance/attendanceregister/add/").status_code, 403)


class CsvSafetyTests(TestCase):
    def test_formula_cells_are_neutralised(self):
        self.assertEqual(safe_cell("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(safe_cell("+1"), "'+1")
        self.assertEqual(safe_cell("@cmd"), "'@cmd")
        self.assertEqual(safe_cell("Normal Name"), "Normal Name")
        self.assertEqual(safe_cell(None), "")
        self.assertEqual(safe_cell(5), "5")
