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
    labels = re.findall(r'<span class="lbl">([^<]+)</span>', block.group(0)) if block else []
    return [l for l in labels if l != "Collapse menu"]


class SidebarTests(Base):
    def labels(self, user, path="/admin/"):
        self.client.force_login(user)
        return nav_labels(self.client.get(path).content.decode())

    def test_principal_sees_everything_including_settings(self):
        self.assertEqual(self.labels(self.principal), ["Overview", "Students", "Classes", "Attendance", "Gradebook", "Report cards", "Settings", "Help"])

    def test_registrar_has_no_settings(self):
        self.assertEqual(self.labels(self.registrar), ["Overview", "Students", "Classes", "Attendance", "Gradebook", "Report cards", "Help"])

    def test_teacher_has_no_settings(self):
        self.assertNotIn("Settings", self.labels(self.homeroom))
        self.assertIn("Gradebook", self.labels(self.maths_teacher))

    def test_super_admin_sees_settings(self):
        boss = User.objects.create_user("boss", password="x-long-test-password-1", role=Role.SUPER_ADMIN, school=self.school)
        self.assertIn("Settings", self.labels(boss))

    def current(self, user, path):
        self.client.force_login(user)
        html = self.client.get(path).content.decode()
        return re.findall(r'class="on" aria-current="page"><svg.*?</svg><span class="lbl">([^<]+)</span>', html)

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
            self.assertIn("Overview", response.content.decode())

    def test_home_page_for_a_teacher_with_nothing_assigned_is_friendly(self):
        lonely = User.objects.create_user("lonely", password="x-long-test-password-1", role=Role.TEACHER, school=self.school)
        self.client.force_login(lonely)
        html = self.client.get("/admin/").content.decode()
        self.assertIn("ask the school office", html)


class SettingsSectionTests(Base):
    def page(self, user, path):
        self.client.force_login(user)
        return self.client.get(path).content.decode()

    def test_settings_is_its_own_page_with_sections_and_buttons(self):
        html = self.page(self.principal, "/admin/settings/")
        for needle in ("Academic years", "Terms", "Grade levels", "Subjects", "Assessment types", "Grade bands", "Holidays",
                       "Admins and staff", "Audit log", "School profile"):
            self.assertIn(needle, html)
        self.assertIn(">Manage<", html)
        self.assertIn(">Add<", html)
        self.assertNotIn("Today's registers", html)   # not the overview scrolled down

    def test_admins_section_shows_who_the_admins_are(self):
        html = self.page(self.principal, "/admin/settings/")
        self.assertIn("principal", html)
        self.assertIn("teachers", html)

    def test_read_only_items_have_no_add_button(self):
        html = self.page(self.principal, "/admin/settings/")
        school_block = html[html.index('id="school"'):html.index('id="academic"')]
        self.assertNotIn(">Add<", school_block)
        audit_block = html[html.index('id="activity"'):]
        self.assertNotIn(">Add<", audit_block)

    def test_every_settings_page_has_the_section_tabs(self):
        for path in ("/admin/settings/", "/admin/academics/subject/", "/admin/accounts/user/", "/admin/grades/gradeband/"):
            html = self.page(self.principal, path)
            self.assertIn('class="stabs"', html, path)
            for tab in (">All settings<", ">Academic<", ">Grading<", ">Attendance<", ">Admins<", ">Activity<"):
                self.assertIn(tab, html, path)

    def test_the_current_tab_and_pill_are_marked(self):
        html = self.page(self.principal, "/admin/academics/term/")
        self.assertRegex(html, r'class="on" aria-current="page">Academic<')
        self.assertRegex(html, r'class="on" aria-current="page">Terms<')
        self.assertIn(">Subjects<", html)  # the other pills of the section

    def test_admins_tab_lists_the_staff(self):
        html = self.page(self.principal, "/admin/accounts/user/")
        self.assertRegex(html, r'class="on" aria-current="page">Admins<')
        self.assertIn("maths", html)

    def test_sidebar_settings_link_opens_the_hub(self):
        html = self.page(self.principal, "/admin/")
        self.assertIn('href="/admin/settings/"', html)

    def test_a_teacher_has_no_settings_item_but_the_page_is_safe(self):
        self.assertNotIn("Settings", nav_labels(self.page(self.homeroom, "/admin/")))
        html = self.page(self.homeroom, "/admin/settings/")
        self.assertNotIn("Audit log", html)
        self.assertNotIn("Staff accounts", html)

    def test_settings_hub_requires_sign_in(self):
        self.assertEqual(self.client.get("/admin/settings/").status_code, 302)


class ShellFeatureTests(Base):
    def page(self, user, path="/admin/"):
        self.client.force_login(user)
        return self.client.get(path).content.decode()

    def test_sidebar_can_collapse_and_is_pinned(self):
        html = self.page(self.principal)
        self.assertIn('class="side-toggle"', html)
        self.assertIn("core/shell.js", html)
        self.assertIn("yool.side", html)                     # remembers the choice
        from django.contrib.staticfiles import finders

        css = open(finders.find("core/theme.css"), encoding="utf-8").read()
        rule = css[css.index(".yool-side {"):css.index("}", css.index(".yool-side {"))]
        self.assertIn("position: sticky", rule)              # stays put while the page scrolls
        self.assertIn("overflow-y: auto", rule)
        self.assertIn("side-collapsed", css)

    def test_every_menu_item_has_a_tooltip_for_the_collapsed_state(self):
        html = self.page(self.principal)
        self.assertIn('title="Gradebook"', html)

    def test_student_search_in_the_header_for_people_who_may_see_students(self):
        self.assertIn('role="search"', self.page(self.principal))
        self.assertIn('action="/admin/people/student/"', self.page(self.principal))
        from accounts.models import User

        nobody = User.objects.create_user("none", password="x-long-test-password-1", school=self.school, is_staff=True)  # no role, no permissions
        self.assertNotIn('role="search"', self.page(nobody))

    def test_header_search_finds_a_student(self):
        self.client.force_login(self.principal)
        html = self.client.get("/admin/people/student/?q=Cali").content.decode()
        self.assertIn("Cali", html)
        self.assertNotIn("Abdi", html)

    def test_help_page_is_role_aware(self):
        teacher_help = self.page(self.homeroom, "/admin/help/")
        self.assertIn("Take attendance", teacher_help)
        self.assertNotIn("Add students", teacher_help)
        self.assertIn("Add students", self.page(self.registrar, "/admin/help/"))
        self.assertRegex(self.page(self.principal, "/admin/help/"), r'class="on" aria-current="page"><svg.*?</svg><span class="lbl">Help</span>')

    def test_forms_have_no_popup_shortcut_icons(self):
        self.client.force_login(self.principal)
        html = self.client.get("/admin/grades/assessment/add/").content.decode()
        self.assertIn("<select", html)
        self.assertNotIn("add-related", html)
        self.assertNotIn("change-related", html)
        self.assertNotIn("delete-related", html)
        self.assertNotIn("view-related", html)

    def test_inline_forms_hide_the_shortcuts_too(self):
        self.client.force_login(self.principal)
        html = self.client.get(f"/admin/academics/classsection/{self.section.pk}/change/").content.decode()
        self.assertNotIn("add-related", html)

    def test_login_page_is_standalone_and_has_no_marketing_line(self):
        html = self.client.get("/admin/login/").content.decode()
        self.assertNotIn("Attendance, grades", html)
        self.assertNotIn("in one place", html)
        self.assertIn("Student Management System", html)
        self.assertNotIn('id="content"', html)               # not inside the padded admin layout
        self.assertNotIn("yool-side", html)

    def test_kpi_numbers_are_moderate(self):
        from django.contrib.staticfiles import finders

        css = open(finders.find("core/theme.css"), encoding="utf-8").read()
        rule = css[css.index(".kpi b {"):css.index("}", css.index(".kpi b {"))]
        self.assertIn("font-size: 22px", rule)

    def test_buttons_for_actions_use_the_theme(self):
        from django.contrib.staticfiles import finders

        css = open(finders.find("core/theme.css"), encoding="utf-8").read()
        for needle in (".object-tools a", ".submit-row input[type=submit]", "#changelist .actions button.button", ".btn.danger", "[hidden] { display: none !important; }", "top: 72px"):
            self.assertIn(needle, css)


class RankWordingTests(Base):
    def test_rank_not_position(self):
        from grades.models import Assessment
        from reports.models import ReportComment

        self.client.force_login(self.principal)
        quiz = self.assess("Quiz 1")
        self.scores(quiz, [20, 10, 5])
        grid = self.client.get(f"/admin/grades/assessment/grid/{self.maths.pk}/{self.term.pk}/").content.decode()
        self.assertIn(">Rank<", grid)
        self.assertNotIn("Position", grid)
        comments = self.client.get(f"/admin/reports/reportcomment/comments/{self.section.pk}/{self.term.pk}/").content.decode()
        self.assertIn("rank 1 of 3", comments)
        self.assertNotIn("position", comments)

    def test_report_card_pdf_says_rank(self):
        from grades.models import Assessment
        from reports.pdf import render_report_cards
        from reports.services import build_report_cards

        self.scores(self.assess("Quiz 1"), [20, 10, 5])
        data = render_report_cards(build_report_cards(self.section, self.term), compress=False)
        self.assertIn(b"Class rank", data)
        self.assertIn(b"Rank", data)
        self.assertNotIn(b"Position", data)
