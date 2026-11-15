import re
from datetime import date

from django.test import TestCase, override_settings

from academics.models import Enrollment
from attendance.models import AttendanceRegister
from core.dashboard import build_dashboard
from grades.tests.test_grades import Base
from accounts.models import User, Role

MON = date(2026, 10, 5)  # a Monday inside Term 1 of the shared fixture
FRI = date(2026, 10, 9)  # Friday: non-teaching by default


def nav_labels(html):
    block = re.search(r'<nav class="yool-side".*?</nav>', html, re.S)
    return re.findall(r"</svg>([^<]+)</a>", block.group(0)) if block else []


class SidebarTests(Base):
    def labels(self, user, path="/admin/"):
        self.client.force_login(user)
        return nav_labels(self.client.get(path).content.decode())

    def test_principal_sees_everything_including_settings(self):
        self.assertEqual(self.labels(self.principal), ["Overview", "Students", "Classes", "Attendance", "Gradebook", "Report cards", "Settings"])

    def test_registrar_has_no_settings(self):
        self.assertEqual(self.labels(self.registrar), ["Overview", "Students", "Classes", "Attendance", "Gradebook", "Report cards"])

    def test_teacher_has_no_settings(self):
        self.assertNotIn("Settings", self.labels(self.homeroom))
        self.assertIn("Gradebook", self.labels(self.maths_teacher))

    def test_super_admin_sees_settings(self):
        boss = User.objects.create_user("boss", password="x-long-test-password-1", role=Role.SUPER_ADMIN, school=self.school)
        self.assertIn("Settings", self.labels(boss))

    def current(self, user, path):
        self.client.force_login(user)
        html = self.client.get(path).content.decode()
        return re.findall(r'<a href="[^"]*" class="on" aria-current="page">.*?</svg>([^<]+)</a>', html)

    def test_current_section_is_marked_once(self):
        self.assertEqual(self.current(self.principal, "/admin/"), ["Overview"])
        self.assertEqual(self.current(self.principal, "/admin/people/student/"), ["Students"])
        self.assertEqual(self.current(self.principal, "/admin/academics/classsection/"), ["Classes"])
        self.assertEqual(self.current(self.principal, "/admin/attendance/attendanceregister/"), ["Attendance"])
        self.assertEqual(self.current(self.principal, "/admin/grades/assessment/"), ["Gradebook"])
        self.assertEqual(self.current(self.principal, "/admin/reports/reportcomment/"), ["Report cards"])

    def test_setup_pages_belong_to_settings(self):
        self.assertEqual(self.current(self.principal, "/admin/academics/term/"), ["Settings"])
        self.assertEqual(self.current(self.principal, "/admin/accounts/user/"), ["Settings"])

    def test_login_page_has_no_sidebar(self):
        self.client.logout()
        html = self.client.get("/admin/login/").content.decode()
        self.assertNotIn("yool-side", html)


class ThemeTests(Base):
    def test_every_page_uses_the_theme_and_is_light_only(self):
        self.client.force_login(self.principal)
        for path in ("/admin/", "/admin/people/student/", "/admin/attendance/attendanceregister/", "/admin/grades/assessment/"):
            html = self.client.get(path).content.decode()
            self.assertIn("core/theme.css", html, path)
            self.assertNotIn("dark_mode.css", html, path)
            self.assertNotIn("theme-toggle", html, path)

    def test_header_shows_logo_and_name_and_logout_is_a_form(self):
        self.client.force_login(self.principal)
        html = self.client.get("/admin/").content.decode()
        self.assertIn("Yool International School", html)
        self.assertIn('class="tile"', html)
        self.assertIn('id="logout-form"', html)

    def test_login_screen_layout(self):
        html = self.client.get("/admin/login/").content.decode()
        for needle in ("login-shell", 'class="hero"', "formcard", "core/show_password.js", "Sign in"):
            self.assertIn(needle, html)

    def test_wrong_password_shows_a_friendly_error(self):
        html = self.client.post("/admin/login/", {"username": "nobody", "password": "wrong", "next": "/admin/"}).content.decode()
        self.assertIn("errornote", html)

    @override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=2)
    def test_lockout_page_is_branded_and_explains(self):
        for _ in range(2):
            response = self.client.post("/admin/login/", {"username": "home", "password": "wrong"}, REMOTE_ADDR="10.1.1.1")
        self.assertEqual(response.status_code, 429)
        html = response.content.decode()
        self.assertIn("Too many sign-in attempts", html)
        self.assertIn("core/theme.css", html)


class DashboardTests(Base):
    def kpi(self, dash, label):
        return next(k for k in dash["kpis"] if k["label"] == label)

    def test_staff_only(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertIsNone(build_dashboard(AnonymousUser()))

    def test_homeroom_teacher_sees_their_register_not_taken_yet(self):
        d = build_dashboard(self.homeroom, MON)
        self.assertTrue(d["show_registers"])
        self.assertEqual([r["section"].display_name for r in d["registers"]], ["Grade 3"])
        self.assertEqual(self.kpi(d, "Registers not taken")["value"], 1)
        self.assertEqual(self.kpi(d, "Students enrolled")["value"], 3)
        self.assertTrue(d["registers"][0]["take_url"].endswith(f"/{MON.isoformat()}/"))
        self.assertTrue(any("not taken yet" in a["text"] for a in d["attention"]))

    def test_after_taking_attendance_the_numbers_update(self):
        e = self.enrollments
        AttendanceRegister.take(self.section, MON, {e[0].pk: ("absent", ""), e[1].pk: ("late", "")}, self.homeroom, today=MON)
        d = build_dashboard(self.homeroom, MON)
        self.assertEqual(self.kpi(d, "Present today")["value"], "2 of 3")   # present + late
        self.assertEqual(self.kpi(d, "Registers not taken")["value"], 0)
        self.assertEqual(self.kpi(d, "Registers not taken")["sub"], "all done")
        self.assertFalse(any("not taken yet" in a["text"] for a in d["attention"]))

    def test_non_teaching_day(self):
        d = build_dashboard(self.homeroom, FRI)
        self.assertEqual(self.kpi(d, "Registers not taken")["sub"], "no school today")
        self.assertEqual(d["registers"][0]["take_url"], "")

    def test_term_card_and_lock_reminder_for_the_principal_only(self):
        d = build_dashboard(self.principal, date(2026, 12, 1))
        term = self.kpi(d, "Term 1")
        self.assertEqual((term["value"], term["sub"]), ("Open", "ends 15 December"))
        self.assertTrue(any("Lock it before printing" in a["text"] for a in d["attention"]))
        self.assertFalse(any("Lock it before printing" in a["text"] for a in build_dashboard(self.homeroom, date(2026, 12, 1))["attention"]))
        self.term.lock()
        self.assertEqual(self.kpi(build_dashboard(self.principal, date(2026, 12, 1)), "Term 1")["value"], "Locked")

    def test_low_attendance_alert(self):
        e = self.enrollments
        for offset in (5, 6, 7, 8, 10):   # five school days (skipping Friday the 9th)
            AttendanceRegister.take(self.section, date(2026, 10, offset), {e[0].pk: ("absent", "")}, self.principal, today=date(2026, 10, 11))
        d = build_dashboard(self.principal, date(2026, 10, 11))
        self.assertTrue(any("below 90% attendance" in a["text"] for a in d["attention"]))

    def test_subject_teacher_sees_subjects_not_registers(self):
        d = build_dashboard(self.pe_teacher, MON)
        self.assertFalse(d["show_registers"])
        self.assertEqual([s["cs"].subject.code for s in d["subjects"]], ["PE"])
        labels = [k["label"] for k in d["kpis"]]
        self.assertIn("Subjects you teach", labels)
        self.assertNotIn("Registers not taken", labels)
        self.assertTrue(d["subjects"][0]["url"].startswith("/admin/grades/assessment/grid/"))

    def test_registrar_sees_every_class_but_no_take_button_without_permission_gap(self):
        d = build_dashboard(self.registrar, MON)
        self.assertEqual(len(d["registers"]), 1)
        self.assertTrue(d["can_take"])  # registrars may take attendance

    def test_home_page_renders_for_every_role(self):
        for user in (self.principal, self.registrar, self.homeroom, self.maths_teacher, self.pe_teacher):
            self.client.force_login(user)
            response = self.client.get("/admin/")
            self.assertEqual(response.status_code, 200, user.username)
            self.assertIn("All records", response.content.decode())

    def test_home_page_for_a_teacher_with_nothing_assigned_is_friendly(self):
        lonely = User.objects.create_user("lonely", password="x-long-test-password-1", role=Role.TEACHER, school=self.school)
        self.client.force_login(lonely)
        html = self.client.get("/admin/").content.decode()
        self.assertIn("ask the school office", html)
