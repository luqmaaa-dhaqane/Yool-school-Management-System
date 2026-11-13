from datetime import date

from academics.models import AcademicYear, Term
from audit.models import AuditLog
from reports.models import ReportComment
from reports.tests.test_reports import ReportBase


class AdminBase(ReportBase):
    def login(self, user):
        self.client.force_login(user)

    def url(self, kind, enrollment=None, term=None):
        parts = [self.section.pk, (term or self.term).pk] + ([enrollment.pk] if enrollment else [])
        return "/admin/reports/reportcomment/{}/{}/".format(kind, "/".join(str(p) for p in parts))


class DashboardTests(AdminBase):
    def dash(self, user):
        self.login(user)
        return self.client.get(f"/admin/reports/reportcomment/?term={self.term.pk}").content.decode()

    def test_homeroom_teacher_sees_their_class(self):
        page = self.dash(self.homeroom)
        self.assertIn("Grade 3", page)
        self.assertIn("Class PDF", page)

    def test_principal_and_registrar_see_classes(self):
        for user in (self.principal, self.registrar):
            self.assertIn("Grade 3", self.dash(user))

    def test_subject_teacher_without_homeroom_class_sees_none(self):
        page = self.dash(self.pe_teacher)
        self.assertNotIn("Class PDF", page)

    def test_open_term_is_flagged_provisional(self):
        self.assertIn("PROVISIONAL", self.dash(self.principal))
        self.term.lock()
        self.assertIn("final", self.dash(self.principal))

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get("/admin/reports/reportcomment/").status_code, 302)


class CommentsPageTests(AdminBase):
    def test_homeroom_teacher_saves_teacher_comments_and_cannot_touch_principal_ones(self):
        self.login(self.homeroom)
        e = self.enrollments[0]
        response = self.client.post(self.url("comments"), {f"teacher_{e.pk}": "Great effort.", f"principal_{e.pk}": "sneaky"})
        self.assertEqual(response.status_code, 302)
        row = ReportComment.objects.get(enrollment=e)
        self.assertEqual((row.teacher_comment, row.principal_comment), ("Great effort.", ""))

    def test_page_marks_unavailable_boxes_read_only(self):
        self.login(self.homeroom)
        page = self.client.get(self.url("comments")).content.decode()
        self.assertEqual(page.count("readonly"), 3)           # all three principal boxes
        self.assertIn("Save comments", page)

    def test_principal_writes_principal_comments(self):
        self.login(self.principal)
        e = self.enrollments[1]
        self.client.post(self.url("comments"), {f"principal_{e.pk}": "Excellent term."})
        self.assertEqual(ReportComment.objects.get(enrollment=e).principal_comment, "Excellent term.")

    def test_locked_term_makes_teacher_boxes_read_only(self):
        self.term.lock()
        self.login(self.homeroom)
        page = self.client.get(self.url("comments")).content.decode()
        self.assertEqual(page.count("readonly"), 6)
        self.assertNotIn("Save comments", page)

    def test_registrar_can_look_but_not_save(self):
        self.login(self.registrar)
        e = self.enrollments[0]
        page = self.client.get(self.url("comments")).content.decode()
        self.assertNotIn("Save comments", page)
        self.client.post(self.url("comments"), {f"teacher_{e.pk}": "x"})
        self.assertEqual(ReportComment.objects.count(), 0)

    def test_subject_teacher_cannot_open_the_page(self):
        self.login(self.pe_teacher)
        self.assertEqual(self.client.get(self.url("comments")).status_code, 404)

    def test_term_from_another_year_is_refused(self):
        other = AcademicYear.objects.create(school=self.school, name="Z", starts_on=date(2027, 9, 1), ends_on=date(2028, 6, 30))
        foreign = Term.objects.create(academic_year=other, name="T", starts_on=date(2027, 9, 1), ends_on=date(2027, 12, 1))
        self.login(self.principal)
        self.assertEqual(self.client.get(self.url("comments", term=foreign)).status_code, 403)

    def test_too_long_comment_is_rejected_with_a_message_and_nothing_saved(self):
        self.login(self.principal)
        e = self.enrollments[0]
        self.client.post(self.url("comments"), {f"teacher_{e.pk}": "x" * 600})
        self.assertEqual(ReportComment.objects.count(), 0)


class PdfDownloadTests(AdminBase):
    def test_class_pdf(self):
        self.login(self.homeroom)
        response = self.client.get(self.url("pdf"))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("report-cards-Grade-3-Term-1.pdf", response["Content-Disposition"])
        self.assertTrue(AuditLog.objects.filter(action="report_cards_generated", user=self.homeroom).exists())

    def test_student_pdf(self):
        self.login(self.principal)
        response = self.client.get(self.url("pdf", enrollment=self.enrollments[0]))
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("report-card-Abdi-T.pdf", response["Content-Disposition"])

    def test_registrar_can_download(self):
        self.login(self.registrar)
        self.assertEqual(self.client.get(self.url("pdf")).status_code, 200)

    def test_subject_teacher_cannot_download(self):
        self.login(self.pe_teacher)
        self.assertEqual(self.client.get(self.url("pdf")).status_code, 404)

    def test_other_teacher_cannot_download_this_class(self):
        self.login(self.maths_teacher)
        self.assertEqual(self.client.get(self.url("pdf")).status_code, 404)

    def test_enrollment_from_another_class_is_404(self):
        from academics.models import ClassSection, Enrollment, GradeLevel
        from people.models import Student

        other = ClassSection.objects.create(academic_year=self.year, grade_level=GradeLevel.objects.create(school=self.school, name="Grade 4", sort_order=4))
        stranger = Student.objects.create(school=self.school, first_name="Z", last_name="Z", gender="male", date_of_birth=date(2017, 1, 1))
        e = Enrollment.enroll(stranger, other, on=date(2026, 9, 1))
        self.login(self.principal)
        self.assertEqual(self.client.get(self.url("pdf", enrollment=e)).status_code, 404)

    def test_anonymous_cannot_download(self):
        self.assertEqual(self.client.get(self.url("pdf")).status_code, 302)
