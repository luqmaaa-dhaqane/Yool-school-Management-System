from datetime import date
from decimal import Decimal

from audit.models import AuditLog
from grades.models import Assessment
from grades.tests.test_admin import AdminPageBase


class QuickAssessmentTests(AdminPageBase):
    def add_url(self, cs=None, term=None):
        return f"/admin/grades/assessment/quick/add/{(cs or self.maths).pk}/{(term or self.term).pk}/"

    def data(self, **kw):
        data = {"title": "Midterm", "assessment_type": str(self.mid.pk), "max_score": "50", "held_on": "2026-10-20"}
        data.update(kw)
        return data

    # --- the dialog is on the page, not a separate page ---
    def test_grid_page_has_the_dialog_and_no_separate_add_link(self):
        self.login(self.maths_teacher)
        page = self.client.get(self.grid()).content.decode()
        self.assertIn('<dialog class="modal" id="assess-dialog"', page)
        self.assertIn(self.add_url(), page)
        self.assertNotIn("/admin/grades/assessment/add/", page)
        self.assertIn(">Edit<", page)

    def test_dashboard_has_the_dialog_for_teachers_and_not_for_registrars(self):
        self.login(self.maths_teacher)
        self.assertIn('id="assess-dialog"', self.client.get(f"/admin/grades/assessment/?term={self.term.pk}").content.decode())
        self.login(self.registrar)
        self.assertNotIn('id="assess-dialog"', self.client.get(f"/admin/grades/assessment/?term={self.term.pk}").content.decode())

    # --- add ---
    def test_add_creates_the_assessment_and_returns_to_the_grid(self):
        self.login(self.maths_teacher)
        response = self.client.post(self.add_url(), self.data())
        self.assertRedirects(response, self.grid(), fetch_redirect_response=False)
        a = Assessment.objects.get(title="Midterm")
        self.assertEqual((a.max_score, a.assessment_type, a.held_on), (Decimal("50.00"), self.mid, date(2026, 10, 20)))
        self.assertTrue(AuditLog.objects.filter(action="create", entity="grades.Assessment").exists())
        self.assertContains(self.client.get(self.grid()), "added. You can enter scores now.")

    def test_date_is_optional_and_comma_decimals_work(self):
        self.login(self.maths_teacher)
        self.client.post(self.add_url(), self.data(title="Quiz 2", max_score="12,5", held_on=""))
        a = Assessment.objects.get(title="Quiz 2")
        self.assertEqual((a.max_score, a.held_on), (Decimal("12.50"), None))

    def test_bad_input_shows_a_message_and_saves_nothing(self):
        self.login(self.maths_teacher)
        for bad, expected in [({"title": " "}, "title"), ({"max_score": "abc"}, "must be a number"), ({"max_score": "0"}, "more than zero"),
                              ({"assessment_type": "nope"}, "assessment type"), ({"held_on": "2027-02-01"}, "inside the term"),
                              ({"held_on": "31/12/2026"}, "date is not valid")]:
            response = self.client.post(self.add_url(), self.data(**bad), follow=True)
            self.assertContains(response, expected)
        self.assertFalse(Assessment.objects.filter(title="Midterm").exists())

    def test_duplicate_title_is_refused_politely(self):
        self.login(self.maths_teacher)
        response = self.client.post(self.add_url(), self.data(title="Quiz 1"), follow=True)
        self.assertContains(response, "already exists")

    def test_locked_term_refuses(self):
        self.term.lock()
        self.login(self.principal)
        self.assertContains(self.client.post(self.add_url(), self.data(), follow=True), "locked")
        self.assertFalse(Assessment.objects.filter(title="Midterm").exists())

    def test_only_the_subject_teacher_or_principal_may_add(self):
        self.login(self.pe_teacher)
        self.assertEqual(self.client.post(self.add_url(), self.data()).status_code, 404)   # not their subject
        self.login(self.registrar)
        self.assertEqual(self.client.post(self.add_url(), self.data()).status_code, 403)
        self.login(self.principal)
        self.assertEqual(self.client.post(self.add_url(), self.data()).status_code, 302)

    def test_get_just_goes_back_to_the_grid(self):
        self.login(self.maths_teacher)
        self.assertEqual(self.client.get(self.add_url()).status_code, 302)

    # --- edit ---
    def edit_url(self, a=None):
        return f"/admin/grades/assessment/quick/{(a or self.quiz1).pk}/edit/"

    def test_edit_changes_title_type_and_maximum(self):
        self.login(self.maths_teacher)
        self.client.post(self.edit_url(), self.data(title="Quiz one", assessment_type=str(self.quiz.pk), max_score="25"))
        self.quiz1.refresh_from_db()
        self.assertEqual((self.quiz1.title, self.quiz1.max_score), ("Quiz one", Decimal("25.00")))

    def test_edit_cannot_lower_the_maximum_below_existing_scores(self):
        self.login(self.maths_teacher)
        response = self.client.post(self.edit_url(), self.data(title="Quiz 1", assessment_type=str(self.quiz.pk), max_score="10"), follow=True)
        self.assertContains(response, "higher than that maximum")
        self.quiz1.refresh_from_db()
        self.assertEqual(self.quiz1.max_score, Decimal("20.00"))

    def test_edit_to_an_existing_title_is_refused(self):
        other = self.assess("Quiz 2")
        self.login(self.maths_teacher)
        self.assertContains(self.client.post(self.edit_url(other), self.data(title="Quiz 1", assessment_type=str(self.quiz.pk)), follow=True), "already exists")

    def test_other_teachers_cannot_edit(self):
        self.login(self.pe_teacher)
        self.assertEqual(self.client.post(self.edit_url(), self.data()).status_code, 404)

    # --- delete ---
    def delete_url(self, a):
        return f"/admin/grades/assessment/quick/{a.pk}/delete/"

    def test_an_assessment_with_scores_cannot_be_deleted(self):
        self.login(self.maths_teacher)
        self.assertContains(self.client.post(self.delete_url(self.quiz1), follow=True), "already has scores")
        self.assertTrue(Assessment.objects.filter(pk=self.quiz1.pk).exists())

    def test_an_empty_assessment_can_be_deleted_and_is_logged(self):
        empty = self.assess("Quiz 9")
        self.login(self.maths_teacher)
        self.client.post(self.delete_url(empty))
        self.assertFalse(Assessment.objects.filter(pk=empty.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete", entity="grades.Assessment").exists())

    def test_delete_needs_post_and_permission(self):
        empty = self.assess("Quiz 9")
        self.login(self.maths_teacher)
        self.client.get(self.delete_url(empty))
        self.assertTrue(Assessment.objects.filter(pk=empty.pk).exists())
        self.login(self.registrar)
        self.assertEqual(self.client.post(self.delete_url(empty)).status_code, 403)
