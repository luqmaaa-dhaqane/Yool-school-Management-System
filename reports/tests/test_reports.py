import re
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from academics.models import AcademicYear, ClassSection, Term
from attendance.models import AttendanceRegister
from audit.models import AuditLog
from core.exceptions import DomainError
from grades.models import GradeBand
from grades.tests.test_grades import Base
from people.models import Student
from reports.models import MAX_COMMENT, ReportComment
from reports.pdf import render_report_cards
from reports.services import build_report_cards, grade_key

D = Decimal


class ReportBase(Base):
    def setUp(self):
        super().setUp()
        for label, minimum, remark in [("A", 90, "Excellent"), ("B", 80, "Very good"), ("C", 70, "Good"), ("F", 0, "Fail")]:
            GradeBand.objects.create(school=self.school, label=label, min_percent=minimum, remark=remark)
        quiz = self.assess("Quiz 1")
        exam = self.assess("Exam", self.exam, 100)
        self.scores(quiz, [20, 10, None])        # maths quiz: 100, 50, -
        self.scores(exam, [90, 60, None])        # maths exam: 90, 60, -
        pe_quiz = self.assess("PE quiz", cs=self.pe)
        pe_quiz.record_scores({e.pk: v for e, v in zip(self.enrollments, [10, 20, 20])}, self.pe_teacher)  # pe: 50, 100, 100

    def cards(self, **kw):
        return {c.student.first_name: c for c in build_report_cards(self.section, self.term, **kw)}


class ReportCardDataTests(ReportBase):
    def test_subjects_percentages_and_overall(self):
        abdi = self.cards()["Abdi"]
        self.assertEqual({l.subject for l in abdi.lines}, {"Mathematics", "Physical Education"})
        maths = next(l for l in abdi.lines if l.subject == "Mathematics")
        self.assertEqual(maths.by_type, {"Exam": D("90.0"), "Quiz": D("100.0")})   # exam weighs 50, quiz 20
        self.assertEqual(maths.percent, D("92.9"))                                  # (90*50 + 100*20) / 70
        self.assertEqual(maths.label, "A")
        self.assertEqual(maths.remark, "Excellent")
        self.assertEqual(abdi.overall, D("71.5"))                                   # mean of the printed 92.9 and 50.0 = 71.45
        self.assertEqual(abdi.overall_label, "C")

    def test_positions(self):
        cards = self.cards()
        self.assertEqual((cards["Cali"].rank, cards["Cali"].class_size), (1, 3))      # only PE: 100
        self.assertEqual((cards["Bilal"].overall, cards["Bilal"].rank), (D("78.6"), 2))
        self.assertEqual((cards["Abdi"].overall, cards["Abdi"].rank), (D("71.5"), 3))
        maths = next(l for l in cards["Abdi"].lines if l.subject == "Mathematics")
        self.assertEqual((maths.rank, maths.ranked_students), (1, 2))                # Cali has no maths scores

    def test_student_without_scores_in_a_subject_shows_dash_not_zero(self):
        cali = self.cards()["Cali"]
        maths = next(l for l in cali.lines if l.subject == "Mathematics")
        self.assertIsNone(maths.percent)
        self.assertIsNone(maths.rank)

    def test_subjects_without_assessments_are_left_out(self):
        self.assertEqual(len(self.cards()["Abdi"].lines), 2)

    def test_attendance_summary_and_days_open(self):
        e = self.enrollments
        AttendanceRegister.take(self.section, date(2026, 10, 5), {e[0].pk: ("absent", "")}, self.homeroom, today=date(2026, 10, 6))
        AttendanceRegister.take(self.section, date(2026, 10, 6), {e[0].pk: ("late", "")}, self.homeroom, today=date(2026, 10, 6))
        card = self.cards()["Abdi"]
        self.assertEqual((card.days_open, card.attendance.absent, card.attendance.late, card.attendance.days), (2, 1, 1, 2))
        self.assertEqual(card.attendance.rate, 50.0)

    def test_provisional_until_the_term_is_locked(self):
        self.assertTrue(self.cards()["Abdi"].provisional)
        self.term.lock()
        self.assertFalse(self.cards()["Abdi"].provisional)

    def test_comments_are_included(self):
        ReportComment.write(self.enrollments[0], self.term, self.homeroom, teacher_comment="Works hard.")
        ReportComment.write(self.enrollments[0], self.term, self.principal, principal_comment="Well done.")
        card = self.cards()["Abdi"]
        self.assertEqual((card.teacher_comment, card.principal_comment), ("Works hard.", "Well done."))

    def test_single_student_selection(self):
        only = build_report_cards(self.section, self.term, enrollment_ids={self.enrollments[1].pk})
        self.assertEqual([c.student.first_name for c in only], ["Bilal"])

    def test_grade_key_ranges(self):
        self.assertEqual(grade_key(self.school), [("A", "90-100", "Excellent"), ("B", "80-89", "Very good"),
                                                  ("C", "70-79", "Good"), ("F", "0-69", "Fail")])

    def test_no_grades_term(self):
        card = {c.student.first_name: c for c in build_report_cards(self.section, self.term2)}["Abdi"]
        self.assertFalse(card.has_grades)
        self.assertIsNone(card.overall)

    def test_class_homeroom_teacher_name(self):
        self.assertEqual(self.cards()["Abdi"].homeroom_teacher, "home")


class CommentRuleTests(ReportBase):
    def write(self, actor, **kw):
        return ReportComment.write(self.enrollments[0], self.term, actor, **kw)

    def test_homeroom_teacher_writes_teacher_comment_only(self):
        self.write(self.homeroom, teacher_comment="  Kind and curious.  ")
        self.assertEqual(ReportComment.objects.get().teacher_comment, "Kind and curious.")
        with self.assertRaisesMessage(DomainError, "Principal"):
            self.write(self.homeroom, principal_comment="Nope")

    def test_subject_teacher_and_registrar_cannot_comment(self):
        for user in (self.maths_teacher, self.pe_teacher, self.registrar):
            with self.assertRaises(DomainError):
                self.write(user, teacher_comment="x")

    def test_principal_writes_both(self):
        self.write(self.principal, teacher_comment="a", principal_comment="b")
        row = ReportComment.objects.get()
        self.assertEqual((row.teacher_comment, row.principal_comment), ("a", "b"))

    def test_locked_term_stops_the_teacher_but_not_the_principal(self):
        self.write(self.homeroom, teacher_comment="before")
        self.term.lock()
        with self.assertRaises(DomainError):
            self.write(self.homeroom, teacher_comment="after")
        self.write(self.principal, teacher_comment="principal fixes a typo", principal_comment="ok")
        self.assertEqual(ReportComment.objects.get().teacher_comment, "principal fixes a typo")

    def test_none_leaves_the_other_comment_alone_and_unchanged_values_need_no_permission(self):
        self.write(self.principal, teacher_comment="t", principal_comment="p")
        self.write(self.homeroom, teacher_comment="t2")
        self.assertEqual(ReportComment.objects.get().principal_comment, "p")
        self.write(self.homeroom, teacher_comment="t2", principal_comment="p")  # unchanged principal text is not a change
        self.assertEqual(ReportComment.objects.count(), 1)

    def test_length_limit(self):
        with self.assertRaisesMessage(DomainError, str(MAX_COMMENT)):
            self.write(self.principal, teacher_comment="x" * (MAX_COMMENT + 1))

    def test_term_from_another_year_rejected(self):
        other = AcademicYear.objects.create(school=self.school, name="Z", starts_on=date(2027, 9, 1), ends_on=date(2028, 6, 30))
        foreign = Term.objects.create(academic_year=other, name="T", starts_on=date(2027, 9, 1), ends_on=date(2027, 12, 1))
        with self.assertRaisesMessage(DomainError, "different academic year"):
            ReportComment.write(self.enrollments[0], foreign, self.principal, teacher_comment="x")

    def test_audit_log(self):
        self.write(self.homeroom, teacher_comment="hello")
        self.assertTrue(AuditLog.objects.filter(action="report_comment").exists())


class PdfTests(ReportBase):
    def pdf(self, **kw):
        return render_report_cards(build_report_cards(self.section, self.term, **kw), compress=False)

    def test_one_page_per_student_with_real_content(self):
        data = self.pdf()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(len(re.findall(rb"/Type /Page[^s]", data)), 3)
        for needle in (b"Student Report Card", b"Abdi T", b"Mathematics", b"PROVISIONAL", b"Grading key"):
            self.assertIn(needle, data)

    def test_locked_term_has_no_provisional_banner(self):
        self.term.lock()
        self.assertNotIn(b"PROVISIONAL", self.pdf())

    def test_single_student_pdf(self):
        data = self.pdf(enrollment_ids={self.enrollments[0].pk})
        self.assertEqual(len(re.findall(rb"/Type /Page[^s]", data)), 1)
        self.assertNotIn(b"Bilal", data)

    def test_special_characters_do_not_break_the_pdf(self):
        s = Student.objects.get(first_name="Abdi")
        s.first_name, s.last_name = "Abdi & <b>Sons</b>", "O'Brien"
        s.save()
        ReportComment.write(self.enrollments[0], self.term, self.principal, teacher_comment="Line one\nLine <two> & more")
        self.assertTrue(self.pdf().startswith(b"%PDF"))

    def test_empty_class_still_produces_a_pdf(self):
        self.assertTrue(render_report_cards([]).startswith(b"%PDF"))

    @override_settings(SITE_LOGO_STATIC="core/show_password.js")  # exists, but is not an image
    def test_unreadable_logo_never_stops_report_cards(self):
        self.assertTrue(self.pdf().startswith(b"%PDF"))

    def test_compressed_pdf_is_valid_too(self):
        self.assertTrue(render_report_cards(build_report_cards(self.section, self.term)).startswith(b"%PDF"))


class OneClassPerGradeTests(TestCase):
    def setUp(self):
        from academics.models import GradeLevel
        from core.models import School

        self.school = School.objects.create(name="S")
        self.year = AcademicYear.objects.create(school=self.school, name="Y", starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30), is_current=True)
        self.grade = GradeLevel.objects.create(school=self.school, name="Grade 1", sort_order=1)

    def test_a_grade_has_one_class_per_year(self):
        from django.db import IntegrityError, transaction

        ClassSection.objects.create(academic_year=self.year, grade_level=self.grade)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ClassSection.objects.create(academic_year=self.year, grade_level=self.grade)

    def test_same_grade_in_another_year_is_fine_and_the_name_is_just_the_grade(self):
        other = AcademicYear.objects.create(school=self.school, name="Z", starts_on=date(2027, 9, 1), ends_on=date(2028, 6, 30))
        a = ClassSection.objects.create(academic_year=self.year, grade_level=self.grade)
        b = ClassSection.objects.create(academic_year=other, grade_level=self.grade)
        self.assertEqual((str(a), a.display_name, b.display_name), ("Grade 1", "Grade 1", "Grade 1"))

    def test_class_form_has_no_section_field(self):
        from accounts.models import Role, User
        from accounts.permissions import sync_role_groups

        sync_role_groups()
        user = User.objects.create_user("p", password="x-long-test-password-1", role=Role.PRINCIPAL, school=self.school)
        self.client.force_login(user)
        page = self.client.get("/admin/academics/classsection/add/").content.decode()
        self.assertNotIn('name="name"', page)
        self.assertIn('name="grade_level"', page)

    def test_importer_places_students_by_grade_and_ignores_an_old_section_column(self):
        from people.importers import StudentImporter

        ClassSection.objects.create(academic_year=self.year, grade_level=self.grade)
        text = "first_name,last_name,gender,date_of_birth,grade,section\nAli,Omar,male,2019-02-02,Grade 1,B\nSara,Omar,F,2019-03-03,1,\n"
        report = StudentImporter(self.school).run(text.encode(), dry_run=False)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(report.enrolled, 2)

    def test_importer_reports_an_unknown_grade(self):
        from people.importers import StudentImporter

        text = "first_name,last_name,gender,date_of_birth,grade\nAli,Omar,male,2019-02-02,Grade 9\n"
        report = StudentImporter(self.school).run(text.encode())
        self.assertIn("No class 'Grade 9'", report.errors[0][1])

    def test_import_template_has_no_section_column(self):
        from people.importers import TEMPLATE_HEADER

        self.assertNotIn("section", TEMPLATE_HEADER)
        self.assertIn("grade", TEMPLATE_HEADER)

    def test_seed_makes_exactly_one_class_per_grade(self):
        from io import StringIO

        from django.core.management import call_command

        call_command("seed", school="Test School", admin_password="Seed-Passw0rd-123", grades=4, stdout=StringIO())
        call_command("seed", school="Test School", admin_password="Seed-Passw0rd-123", grades=4, stdout=StringIO())
        self.assertEqual(ClassSection.objects.filter(academic_year__school__name="Test School").count(), 4)
