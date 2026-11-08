from datetime import date

from django.test import TestCase

from academics.models import AcademicYear, ClassSection, Enrollment, GradeLevel
from academics.services import graduate_section, promote_section
from core.exceptions import DomainError
from core.models import School
from people.models import Student


class Base(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="S")
        self.y1 = AcademicYear.objects.create(
            school=self.school, name="2026/27", starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30), is_current=True)
        self.y2 = AcademicYear.objects.create(
            school=self.school, name="2027/28", starts_on=date(2027, 9, 1), ends_on=date(2028, 6, 30))
        self.g = {n: GradeLevel.objects.create(school=self.school, name=f"Grade {n}", sort_order=n) for n in (1, 2, 3)}
        self.s1a = ClassSection.objects.create(academic_year=self.y1, grade_level=self.g[1], name="A", capacity=2)
        self.s1b = ClassSection.objects.create(academic_year=self.y1, grade_level=self.g[1], name="B")
        self.s2a_next = ClassSection.objects.create(academic_year=self.y2, grade_level=self.g[2], name="A")
        self.s1a_next = ClassSection.objects.create(academic_year=self.y2, grade_level=self.g[1], name="A")

    def student(self, first="A", **kw):
        data = dict(first_name=first, last_name="Test", gender="male", date_of_birth=date(2019, 1, 1))
        data.update(kw)
        return Student.objects.create(school=self.school, **data)


class EnrollmentRuleTests(Base):
    def test_enroll_and_roster(self):
        a, b = self.student("Zed"), self.student("Abe")
        Enrollment.enroll(a, self.s1a)
        Enrollment.enroll(b, self.s1a)
        self.assertEqual([e.student.first_name for e in self.s1a.roster()], ["Abe", "Zed"])
        self.assertEqual(Enrollment.objects.first().academic_year, self.y1)

    def test_capacity_enforced(self):
        Enrollment.enroll(self.student("A"), self.s1a)
        Enrollment.enroll(self.student("B"), self.s1a)
        self.assertFalse(self.s1a.has_capacity())
        with self.assertRaisesMessage(DomainError, "is full"):
            Enrollment.enroll(self.student("C"), self.s1a)

    def test_seats_left(self):
        Enrollment.enroll(self.student("A"), self.s1a)
        self.assertEqual(self.s1a.seats_left(), 1)
        self.assertIsNone(self.s1b.seats_left())

    def test_one_class_per_year(self):
        s = self.student()
        Enrollment.enroll(s, self.s1a)
        with self.assertRaisesMessage(DomainError, "already enrolled"):
            Enrollment.enroll(s, self.s1b)

    def test_inactive_student_cannot_enroll(self):
        s = self.student()
        s.suspend()
        with self.assertRaises(DomainError):
            Enrollment.enroll(s, self.s1a)

    def test_locked_year_blocks_enrollment(self):
        self.y1.lock()
        with self.assertRaisesMessage(DomainError, "locked"):
            Enrollment.enroll(self.student(), self.s1a)

    def test_other_school_rejected(self):
        outsider = Student.objects.create(
            school=School.objects.create(name="X"), first_name="O", last_name="X", gender="male",
            date_of_birth=date(2019, 1, 1))
        with self.assertRaises(DomainError):
            Enrollment.enroll(outsider, self.s1a)

    def test_current_class(self):
        s = self.student()
        self.assertIsNone(s.current_class())
        Enrollment.enroll(s, self.s1a)
        self.assertEqual(s.current_class(), self.s1a)


class StatusChangeTests(Base):
    def test_withdraw_closes_enrollment_and_updates_student(self):
        s = self.student()
        e = Enrollment.enroll(s, self.s1a)
        e.withdraw(reason="Moved away")
        e.refresh_from_db(); s.refresh_from_db()
        self.assertEqual(e.status, Enrollment.Status.WITHDRAWN)
        self.assertEqual(s.status, Student.Status.WITHDRAWN)
        self.assertIsNotNone(e.left_on)
        self.assertIn("Moved away", e.notes)
        self.assertEqual(self.s1a.enrolled_count(), 0)

    def test_closed_enrollment_cannot_change_again(self):
        e = Enrollment.enroll(self.student(), self.s1a)
        e.withdraw()
        with self.assertRaises(DomainError):
            e.withdraw()

    def test_transfer_out_and_graduate(self):
        e = Enrollment.enroll(self.student("A"), self.s1a)
        e.transfer_out()
        self.assertEqual(e.student.status, Student.Status.TRANSFERRED)
        e2 = Enrollment.enroll(self.student("B"), self.s1b)
        e2.graduate()
        self.assertEqual(e2.student.status, Student.Status.GRADUATED)

    def test_readmitted_student_can_enroll_again_same_year(self):
        s = self.student()
        e = Enrollment.enroll(s, self.s1a)
        e.withdraw()
        s.refresh_from_db(); s.reinstate()
        Enrollment.enroll(s, self.s1b)
        self.assertEqual(s.current_class(), self.s1b)


class PromotionTests(Base):
    def test_promote_creates_next_year_enrollment(self):
        s = self.student()
        e = Enrollment.enroll(s, self.s1a)
        new = e.promote(self.s2a_next)
        e.refresh_from_db()
        self.assertEqual(e.status, Enrollment.Status.PROMOTED)
        self.assertEqual(new.class_section, self.s2a_next)
        self.assertEqual(new.academic_year, self.y2)
        self.assertEqual(s.current_class(), self.s2a_next)

    def test_promote_must_go_up_a_grade_and_forward_a_year(self):
        e = Enrollment.enroll(self.student(), self.s1a)
        with self.assertRaisesMessage(DomainError, "higher grade"):
            e.promote(self.s1a_next)
        with self.assertRaisesMessage(DomainError, "later academic year"):
            e.promote(self.s1b)
        e.refresh_from_db()
        self.assertTrue(e.is_active())  # failed promotion changed nothing

    def test_repeat_stays_in_same_grade(self):
        e = Enrollment.enroll(self.student(), self.s1a)
        new = e.repeat(self.s1a_next)
        self.assertEqual(new.class_section, self.s1a_next)
        e.refresh_from_db()
        self.assertEqual(e.status, Enrollment.Status.REPEATED)

    def test_repeat_into_other_grade_rejected(self):
        e = Enrollment.enroll(self.student(), self.s1a)
        with self.assertRaises(DomainError):
            e.repeat(self.s2a_next)

    def test_promote_whole_class_with_repeaters(self):
        a, b = self.student("A"), self.student("B")
        Enrollment.enroll(a, self.s1a)
        Enrollment.enroll(b, self.s1a)
        result = promote_section(self.s1a, self.s2a_next, repeat_students=[b], repeat_section=self.s1a_next)
        self.assertEqual((result.promoted, result.repeated), (1, 1))
        self.assertEqual(a.current_class(), self.s2a_next)
        self.assertEqual(b.current_class(), self.s1a_next)

    def test_promotion_is_all_or_nothing(self):
        small = ClassSection.objects.create(academic_year=self.y2, grade_level=self.g[2], name="B", capacity=1)
        a, b = self.student("A"), self.student("B")
        Enrollment.enroll(a, self.s1a)
        Enrollment.enroll(b, self.s1a)
        with self.assertRaisesMessage(DomainError, "is full"):
            promote_section(self.s1a, small)
        self.assertEqual(self.s1a.enrolled_count(), 2)
        self.assertEqual(small.enrolled_count(), 0)

    def test_repeaters_need_a_target_class(self):
        a = self.student("A")
        Enrollment.enroll(a, self.s1a)
        with self.assertRaises(DomainError):
            promote_section(self.s1a, self.s2a_next, repeat_students=[a])

    def test_graduate_section(self):
        Enrollment.enroll(self.student("A"), self.s1a)
        self.assertEqual(graduate_section(self.s1a).graduated, 1)
        self.assertEqual(self.s1a.enrolled_count(), 0)
