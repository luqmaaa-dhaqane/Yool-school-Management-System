from datetime import date
from decimal import Decimal

from django.test import TestCase

from academics.models import AcademicYear, ClassSection, ClassSubject, Enrollment, GradeLevel, Subject, Term
from accounts.models import Role, User
from accounts.permissions import sync_role_groups
from attendance.models import AttendanceRegister
from core.exceptions import DomainError
from core.models import School
from grades.gradebook import GradeBook, weighted_percent
from grades.models import Assessment, AssessmentType, GradeBand, GradeEntry
from grades.scales import BandScale, PercentScale, scale_for
from grades.services import class_results
from people.models import Student

D = Decimal


class Base(TestCase):
    def setUp(self):
        sync_role_groups()
        self.school = School.objects.create(name="S")
        self.year = AcademicYear.objects.create(
            school=self.school, name="Y", starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30), is_current=True)
        self.term = Term.objects.create(academic_year=self.year, name="Term 1", starts_on=date(2026, 9, 1), ends_on=date(2026, 12, 15))
        self.term2 = Term.objects.create(academic_year=self.year, name="Term 2", starts_on=date(2027, 1, 5), ends_on=date(2027, 3, 31))
        grade = GradeLevel.objects.create(school=self.school, name="Grade 3", sort_order=3)
        mk = lambda name, role: User.objects.create_user(name, password="x-long-test-password-1", role=role, school=self.school)
        self.homeroom, self.maths_teacher, self.pe_teacher = mk("home", Role.TEACHER), mk("maths", Role.TEACHER), mk("pe", Role.TEACHER)
        self.principal, self.registrar = mk("prin", Role.PRINCIPAL), mk("reg", Role.REGISTRAR)
        self.section = ClassSection.objects.create(academic_year=self.year, grade_level=grade, homeroom_teacher=self.homeroom)
        self.maths = ClassSubject.objects.create(class_section=self.section, subject=Subject.objects.create(school=self.school, code="MTH", name="Mathematics"), teacher=self.maths_teacher)
        self.pe = ClassSubject.objects.create(class_section=self.section, subject=Subject.objects.create(school=self.school, code="PE", name="Physical Education"), teacher=self.pe_teacher)
        self.quiz = AssessmentType.objects.create(school=self.school, name="Quiz", weight_percent=20)
        self.mid = AssessmentType.objects.create(school=self.school, name="Midterm", weight_percent=30)
        self.exam = AssessmentType.objects.create(school=self.school, name="Exam", weight_percent=50)
        self.enrollments = []
        for name in ["Abdi", "Bilal", "Cali"]:
            s = Student.objects.create(school=self.school, first_name=name, last_name="T", gender="male", date_of_birth=date(2017, 1, 1))
            self.enrollments.append(Enrollment.enroll(s, self.section, on=date(2026, 9, 1)))

    def assess(self, title="Quiz 1", kind=None, maximum=20, cs=None, term=None, held_on=date(2026, 10, 1)):
        return Assessment.objects.create(class_subject=cs or self.maths, term=term or self.term,
                                         assessment_type=kind or self.quiz, title=title, max_score=maximum, held_on=held_on)

    def scores(self, assessment, values, actor=None):
        return assessment.record_scores({e.pk: v for e, v in zip(self.enrollments, values)}, actor or self.maths_teacher)


class CalculationTests(Base):
    def test_single_assessment_percent(self):
        quiz = self.assess()
        self.scores(quiz, [18, 10, None])
        book = GradeBook(self.maths, self.term)
        self.assertEqual(book.term_percent(self.enrollments[0]), D("90.0"))
        self.assertEqual(book.term_percent(self.enrollments[1]), D("50.0"))
        self.assertIsNone(book.term_percent(self.enrollments[2]))  # no score is not zero

    def test_weights_are_applied(self):
        quiz, mid, exam = self.assess("Q1"), self.assess("Mid", self.mid, 50), self.assess("Exam", self.exam, 100)
        self.scores(quiz, [20, 0, 10])    # 100%, 0%, 50%
        self.scores(mid, [50, 0, 25])     # 100%, 0%, 50%
        self.scores(exam, [100, 100, 0])  # 100%, 100%, 0%
        book = GradeBook(self.maths, self.term)
        self.assertEqual(book.term_percent(self.enrollments[0]), D("100.0"))
        self.assertEqual(book.term_percent(self.enrollments[1]), D("50.0"))   # 0*.2 + 0*.3 + 100*.5
        self.assertEqual(book.term_percent(self.enrollments[2]), D("25.0"))   # 50*.2 + 50*.3 + 0*.5

    def test_missing_exam_rescales_the_other_weights(self):
        self.scores(self.assess("Q1"), [20, 10, 0])
        self.scores(self.assess("Mid", self.mid, 50), [25, 50, 0])
        # Abdi: quiz 100%, mid 50% -> (100*20 + 50*30) / 50 = 70
        self.assertEqual(GradeBook(self.maths, self.term).term_percent(self.enrollments[0]), D("70.0"))

    def test_several_assessments_of_one_type_are_averaged(self):
        self.scores(self.assess("Q1"), [20, 0, 0])
        self.scores(self.assess("Q2"), [10, 0, 0])
        self.assertEqual(GradeBook(self.maths, self.term).term_percent(self.enrollments[0]), D("75.0"))

    def test_weighted_percent_helper(self):
        self.assertIsNone(weighted_percent({}, {}))
        self.assertEqual(weighted_percent({"a": D(80), "b": D(60)}, {"a": D(0), "b": D(0)}), D(70))  # zero weights: plain mean

    def test_rank_and_ties(self):
        self.scores(self.assess(), [20, 20, 10])
        rows = GradeBook(self.maths, self.term).rows()
        self.assertEqual([r["rank"] for r in rows], [1, 1, 3])  # competition ranking

    def test_other_term_scores_are_separate(self):
        self.scores(self.assess(), [20, 20, 20])
        self.assertIsNone(GradeBook(self.maths, self.term2).term_percent(self.enrollments[0]))

    def test_grade_labels(self):
        for label, minimum in [("A", 90), ("B", 80), ("C", 70), ("F", 0)]:
            GradeBand.objects.create(school=self.school, label=label, min_percent=minimum, remark=f"{label} remark")
        scale = scale_for(self.school)
        self.assertIsInstance(scale, BandScale)
        self.assertEqual([scale.label(D(p)) for p in (95, 90, 89.9, 75, 69, 0)], ["A", "A", "B", "C", "F", "F"])
        self.assertEqual(scale.remark(D(95)), "A remark")
        self.assertEqual(scale.label(None), "")

    def test_percent_scale_when_no_bands(self):
        scale = scale_for(self.school)
        self.assertIsInstance(scale, PercentScale)
        self.assertEqual(scale.label(D("72.4")), "72%")


class ScoreRuleTests(Base):
    def setUp(self):
        super().setUp()
        self.quiz1 = self.assess()

    def test_score_must_be_within_range(self):
        for bad in (-1, 21):
            with self.assertRaisesMessage(DomainError, "between 0 and 20"):
                self.scores(self.quiz1, [bad, None, None])
        self.assertEqual(GradeEntry.objects.count(), 0)

    def test_invalid_number_rejected(self):
        with self.assertRaisesMessage(DomainError, "not a valid score"):
            self.scores(self.quiz1, ["abc", None, None])

    def test_all_or_nothing(self):
        with self.assertRaises(DomainError):
            self.scores(self.quiz1, [10, 12, 99])
        self.assertEqual(GradeEntry.objects.count(), 0)

    def test_changing_and_clearing(self):
        self.scores(self.quiz1, [10, 10, 10])
        self.assertEqual(self.scores(self.quiz1, [15, 10, None]), (1, 1))
        self.assertEqual(GradeEntry.objects.get(enrollment=self.enrollments[0]).score, D("15.00"))
        self.assertEqual(GradeEntry.objects.count(), 2)

    def test_only_the_subject_teacher_or_principal_can_grade(self):
        self.assertTrue(self.maths.can_be_graded_by(self.maths_teacher))
        self.assertTrue(self.maths.can_be_graded_by(self.principal))
        for user in (self.pe_teacher, self.homeroom, self.registrar):
            self.assertFalse(self.maths.can_be_graded_by(user), user.username)
            with self.assertRaisesMessage(DomainError, "not allowed"):
                self.scores(self.quiz1, [10, 10, 10], actor=user)
        self.scores(self.quiz1, [10, 10, 10], actor=self.principal)

    def test_other_schools_principal_cannot_grade(self):
        other = School.objects.create(name="Other")
        outsider = User.objects.create_user("out", password="x-long-test-password-1", role=Role.PRINCIPAL, school=other)
        self.assertFalse(self.maths.can_be_graded_by(outsider))

    def test_unknown_student_rejected(self):
        outsider = Student.objects.create(school=self.school, first_name="O", last_name="X", gender="male", date_of_birth=date(2017, 1, 1))
        other_class = ClassSection.objects.create(academic_year=self.year, grade_level=GradeLevel.objects.create(school=self.school, name="Grade 4", sort_order=4))
        e = Enrollment.enroll(outsider, other_class, on=date(2026, 9, 1))
        with self.assertRaisesMessage(DomainError, "do not belong"):
            self.quiz1.record_scores({e.pk: 5}, self.maths_teacher)

    def test_locked_term_blocks_grading(self):
        self.term.lock()
        with self.assertRaisesMessage(DomainError, "locked"):
            self.scores(self.quiz1, [10, 10, 10])
        self.term.unlock()
        self.scores(self.quiz1, [10, 10, 10])

    def test_student_who_left_before_the_term_is_not_listed(self):
        s = Student.objects.create(school=self.school, first_name="Gone", last_name="T", gender="male", date_of_birth=date(2017, 1, 1))
        e = Enrollment.enroll(s, self.section, on=date(2026, 9, 1))
        e.withdraw(on=date(2026, 8, 30))  # left before Term 1 started
        names = [x.student.first_name for x in GradeBook(self.maths, self.term).enrollments]
        self.assertNotIn("Gone", names)

    def test_save_scores_audits(self):
        from audit.models import AuditLog

        GradeBook(self.maths, self.term).save_scores(self.maths_teacher, {(self.quiz1.pk, self.enrollments[0].pk): D(12)})
        self.assertTrue(AuditLog.objects.filter(action="grades_saved").exists())

    def test_assessment_of_another_subject_rejected_by_gradebook(self):
        pe_quiz = self.assess("PE quiz", cs=self.pe)
        with self.assertRaises(DomainError):
            GradeBook(self.maths, self.term).save_scores(self.maths_teacher, {(pe_quiz.pk, self.enrollments[0].pk): D(1)})


class AssessmentRuleTests(Base):
    def test_term_must_belong_to_the_class_year(self):
        other_year = AcademicYear.objects.create(school=self.school, name="Z", starts_on=date(2027, 9, 1), ends_on=date(2028, 6, 30))
        foreign = Term.objects.create(academic_year=other_year, name="T", starts_on=date(2027, 9, 1), ends_on=date(2027, 12, 1))
        with self.assertRaisesMessage(DomainError, "different academic year"):
            self.assess(term=foreign, held_on=None)

    def test_date_must_be_in_term(self):
        with self.assertRaisesMessage(DomainError, "inside the term"):
            self.assess(held_on=date(2027, 2, 1))

    def test_locked_term_blocks_create_and_delete(self):
        quiz = self.assess()
        self.term.lock()
        with self.assertRaises(DomainError):
            self.assess("Quiz 2")
        with self.assertRaises(DomainError):
            quiz.delete()

    def test_assessment_with_scores_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        quiz = self.assess()
        self.scores(quiz, [10, 10, 10])
        with self.assertRaises(ProtectedError):
            quiz.delete()

    def test_title_unique_per_subject_and_term(self):
        from django.db import IntegrityError, transaction

        self.assess()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.assess()


class ClassResultsTests(Base):
    def test_overall_average_rank_and_subject_selection(self):
        self.scores(self.assess(), [20, 10, 0])                              # maths: 100, 50, 0
        pe_quiz = self.assess("PE quiz", cs=self.pe)
        pe_quiz.record_scores({e.pk: v for e, v in zip(self.enrollments, [10, 20, None])}, self.pe_teacher)  # pe: 50, 100, -
        data = class_results(self.section, self.term)
        self.assertEqual({cs.subject.code for cs in data["subjects"]}, {"MTH", "PE"})
        by_name = {r["student"].first_name: r for r in data["rows"]}
        self.assertEqual(by_name["Abdi"]["overall"], D("75.0"))
        self.assertEqual(by_name["Bilal"]["overall"], D("75.0"))
        self.assertEqual(by_name["Cali"]["overall"], D("0.0"))  # only maths counted
        self.assertEqual([by_name[n]["rank"] for n in ("Abdi", "Bilal", "Cali")], [1, 1, 3])

    def test_subjects_without_assessments_are_left_out(self):
        self.scores(self.assess(), [20, 10, 0])
        self.assertEqual([cs.subject.code for cs in class_results(self.section, self.term)["subjects"]], ["MTH"])


class ClassSubjectTests(Base):
    def test_add_all_subjects_is_idempotent(self):
        Subject.objects.create(school=self.school, code="ART", name="Art")
        other = ClassSection.objects.create(academic_year=self.year, grade_level=GradeLevel.objects.create(school=self.school, name="Grade 4", sort_order=4))
        self.assertEqual(other.add_all_subjects(), 3)
        self.assertEqual(other.add_all_subjects(), 0)
        self.assertEqual(other.class_subjects.count(), 3)

    def test_subject_teacher_sees_the_class_but_does_not_manage_it(self):
        self.assertIn(self.section, list(self.pe_teacher.accessible_sections()))
        self.assertEqual(list(self.pe_teacher.managed_sections()), [])
        self.assertEqual(list(self.homeroom.managed_sections()), [self.section])

    def test_subject_teacher_cannot_take_attendance(self):
        with self.assertRaisesMessage(DomainError, "not assigned"):
            AttendanceRegister.take(self.section, date(2026, 10, 5), {}, self.pe_teacher, today=date(2026, 10, 6))
        AttendanceRegister.take(self.section, date(2026, 10, 5), {}, self.homeroom, today=date(2026, 10, 6))
