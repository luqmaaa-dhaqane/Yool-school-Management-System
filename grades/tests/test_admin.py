from datetime import date
from decimal import Decimal

from django.test import TestCase

from academics.models import Enrollment, Term
from attendance.models import AttendanceRegister
from audit.models import AuditLog
from grades.models import Assessment, AssessmentType, GradeEntry
from grades.tests.test_grades import Base


class AdminPageBase(Base):
    def setUp(self):
        super().setUp()
        self.quiz1 = self.assess("Quiz 1", maximum=20)
        self.quiz1.record_scores({e.pk: v for e, v in zip(self.enrollments, [18, 10, None])}, self.maths_teacher)

    def login(self, user):
        self.client.force_login(user)

    def grid(self, cs=None, term=None):
        return f"/admin/grades/assessment/grid/{(cs or self.maths).pk}/{(term or self.term).pk}/"

    def post_scores(self, values, assessment=None, cs=None):
        a = assessment or self.quiz1
        data = {f"score_{a.pk}_{e.pk}": v for e, v in zip(self.enrollments, values)}
        return self.client.post(self.grid(cs), data)


class DashboardTests(AdminPageBase):
    def dash(self, user):
        self.login(user)
        return self.client.get(f"/admin/grades/assessment/?term={self.term.pk}").content.decode()

    def test_subject_teacher_sees_only_their_subject(self):
        page = self.dash(self.maths_teacher)
        self.assertIn("Mathematics", page)
        self.assertNotIn("Physical Education", page)

    def test_principal_and_registrar_see_all_subjects(self):
        for user in (self.principal, self.registrar):
            page = self.dash(user)
            self.assertIn("Mathematics", page)
            self.assertIn("Physical Education", page)

    def test_registrar_gets_view_only_buttons(self):
        page = self.dash(self.registrar)
        self.assertIn(">View<", page)
        self.assertNotIn("Enter grades", page)

    def test_homeroom_teacher_without_subject_sees_nothing_to_grade(self):
        self.assertNotIn("Mathematics", self.dash(self.homeroom))

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get("/admin/grades/assessment/").status_code, 302)


class GridTests(AdminPageBase):
    def test_grid_shows_existing_scores_and_results(self):
        self.login(self.maths_teacher)
        page = self.client.get(self.grid()).content.decode()
        self.assertIn('value="18"', page)
        self.assertIn("90.0", page)      # Abdi 18/20
        self.assertIn("Abdi", page)

    def test_saving_scores(self):
        self.login(self.maths_teacher)
        response = self.post_scores(["20", "5", "12,5"])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(GradeEntry.objects.get(enrollment=self.enrollments[2]).score, Decimal("12.50"))  # comma accepted
        self.assertEqual(GradeEntry.objects.get(enrollment=self.enrollments[1]).score, Decimal("5.00"))
        self.assertTrue(AuditLog.objects.filter(action="grades_saved").exists())

    def test_empty_box_clears_the_score(self):
        self.login(self.maths_teacher)
        self.post_scores(["", "10", ""])
        self.assertFalse(GradeEntry.objects.filter(enrollment=self.enrollments[0]).exists())

    def test_invalid_input_saves_nothing_and_explains(self):
        self.login(self.maths_teacher)
        response = self.post_scores(["19", "abc", "5"])
        self.assertContains(response, "not a number")
        self.assertEqual(GradeEntry.objects.get(enrollment=self.enrollments[0]).score, Decimal("18.00"))

    def test_out_of_range_saves_nothing(self):
        self.login(self.maths_teacher)
        response = self.post_scores(["19", "10", "25"])
        self.assertContains(response, "between 0 and 20")
        self.assertEqual(GradeEntry.objects.get(enrollment=self.enrollments[0]).score, Decimal("18.00"))

    def test_registrar_cannot_change_grades(self):
        self.login(self.registrar)
        page = self.client.get(self.grid()).content.decode()
        self.assertIn("readonly", page)
        self.assertNotIn("Save grades", page)
        self.post_scores(["1", "1", "1"])
        self.assertEqual(GradeEntry.objects.get(enrollment=self.enrollments[0]).score, Decimal("18.00"))

    def test_teacher_cannot_open_another_teachers_subject(self):
        self.login(self.pe_teacher)
        self.assertEqual(self.client.get(self.grid()).status_code, 404)

    def test_principal_can_correct_any_subject(self):
        self.login(self.principal)
        self.post_scores(["7", "7", "7"])
        self.assertEqual(GradeEntry.objects.get(enrollment=self.enrollments[0]).score, Decimal("7.00"))

    def test_locked_term_is_read_only(self):
        self.term.lock()
        self.login(self.maths_teacher)
        page = self.client.get(self.grid()).content.decode()
        self.assertIn("Locked", page)
        self.assertNotIn("Save grades", page)
        self.post_scores(["1", "1", "1"])
        self.assertEqual(GradeEntry.objects.get(enrollment=self.enrollments[0]).score, Decimal("18.00"))


class AssessmentAdminTests(AdminPageBase):
    def add_data(self, **kw):
        data = {"class_subject": self.maths.pk, "term": self.term.pk, "assessment_type": self.mid.pk,
                "title": "Midterm", "max_score": "50", "held_on": "2026-10-20"}
        data.update(kw)
        return data

    def test_teacher_can_add_assessment_for_own_subject(self):
        self.login(self.maths_teacher)
        response = self.client.post("/admin/grades/assessment/add/", self.add_data())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Assessment.objects.filter(title="Midterm").exists())

    def test_teacher_cannot_add_for_other_subject(self):
        self.login(self.maths_teacher)
        response = self.client.post("/admin/grades/assessment/add/", self.add_data(class_subject=self.pe.pk))
        self.assertEqual(response.status_code, 200)  # form re-shown with an error
        self.assertFalse(Assessment.objects.filter(title="Midterm").exists())

    def test_locked_term_rejects_new_assessment(self):
        self.term.lock()
        self.login(self.principal)
        self.client.post("/admin/grades/assessment/add/", self.add_data())
        self.assertFalse(Assessment.objects.filter(title="Midterm").exists())

    def test_teacher_sees_only_own_assessments_in_list_url(self):
        self.login(self.pe_teacher)
        self.assertEqual(self.client.get(f"/admin/grades/assessment/{self.quiz1.pk}/change/").status_code, 302)

    def test_registrar_cannot_add_assessments(self):
        self.login(self.registrar)
        self.assertEqual(self.client.get("/admin/grades/assessment/add/").status_code, 403)

    def test_weights_warning_when_not_100(self):
        self.exam.weight_percent = 40
        self.exam.save()
        self.login(self.principal)
        self.assertContains(self.client.get("/admin/grades/assessmenttype/"), "add up to 90")

    def test_no_warning_when_100(self):
        self.login(self.principal)
        self.assertNotContains(self.client.get("/admin/grades/assessmenttype/"), "add up to")


class MarksheetTests(AdminPageBase):
    def url(self, extra=""):
        return f"/admin/grades/assessment/reports/marksheet/?term={self.term.pk}&section={self.section.pk}{extra}"

    def test_homeroom_teacher_sees_the_class_mark_sheet(self):
        self.login(self.homeroom)
        page = self.client.get(self.url()).content.decode()
        self.assertIn("Mathematics", page)
        self.assertIn("Abdi", page)

    def test_subject_teacher_who_is_not_homeroom_cannot_use_it(self):
        self.login(self.maths_teacher)
        page = self.client.get(self.url()).content.decode()
        self.assertNotIn("Abdi", page)

    def test_csv_download(self):
        self.login(self.principal)
        response = self.client.get(self.url("&format=csv"))
        self.assertEqual(response["Content-Type"], "text/csv")
        text = response.content.decode()
        self.assertIn("Overall %", text)
        self.assertIn("Abdi", text)

    def test_term_from_another_year_rejected(self):
        from academics.models import AcademicYear

        y2 = AcademicYear.objects.create(school=self.school, name="Z", starts_on=date(2027, 9, 1), ends_on=date(2028, 6, 30))
        t2 = Term.objects.create(academic_year=y2, name="T", starts_on=date(2027, 9, 1), ends_on=date(2027, 12, 1))
        self.login(self.principal)
        page = self.client.get(f"/admin/grades/assessment/reports/marksheet/?term={t2.pk}&section={self.section.pk}").content.decode()
        self.assertIn("different academic year", page)


class TermLockAdminTests(AdminPageBase):
    def test_principal_locks_and_unlocks_terms(self):
        self.login(self.principal)
        self.client.post("/admin/academics/term/", {"action": "lock_action", "_selected_action": [self.term.pk]})
        self.term.refresh_from_db()
        self.assertTrue(self.term.is_locked)
        self.assertTrue(AuditLog.objects.filter(action="lock_term").exists())
        self.client.post("/admin/academics/term/", {"action": "unlock_action", "_selected_action": [self.term.pk]})
        self.term.refresh_from_db()
        self.assertFalse(self.term.is_locked)

    def test_teacher_cannot_lock_terms(self):
        self.login(self.maths_teacher)
        self.client.post("/admin/academics/term/", {"action": "lock_action", "_selected_action": [self.term.pk]})
        self.term.refresh_from_db()
        self.assertFalse(self.term.is_locked)


class SubjectTeacherVisibilityTests(AdminPageBase):
    def test_subject_teacher_sees_class_students_but_not_guardians(self):
        from people.models import Guardian

        g = Guardian.objects.create(school=self.school, first_name="Mum", last_name="Of", phone="063")
        self.enrollments[0].student.add_guardian(g, "Mother")
        self.login(self.pe_teacher)
        self.assertIn("Abdi", self.client.get("/admin/people/student/").content.decode())
        self.assertNotIn("Mum", self.client.get("/admin/people/guardian/").content.decode())

    def test_subject_teacher_has_no_attendance_classes(self):
        self.login(self.pe_teacher)
        page = self.client.get("/admin/attendance/attendanceregister/").content.decode()
        self.assertNotIn("Take attendance", page)
        self.assertNotIn("Grade 3", page)

    def test_subject_teacher_cannot_open_take_attendance_page(self):
        self.login(self.pe_teacher)
        url = f"/admin/attendance/attendanceregister/take/{self.section.pk}/2026-10-05/"
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_subject_teacher_sees_the_class_section_and_roster(self):
        self.login(self.pe_teacher)
        self.assertIn("Grade 3", self.client.get("/admin/academics/classsection/").content.decode())
