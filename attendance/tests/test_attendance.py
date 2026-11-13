from datetime import date, timedelta

from django.test import TestCase, override_settings

from academics.models import AcademicYear, ClassSection, Enrollment, GradeLevel, Term
from accounts.models import Role, User
from accounts.permissions import sync_role_groups
from attendance.models import AttendanceRecord, AttendanceRegister, Holiday, Status
from attendance.services import chronic_absentees, class_day_rows, enrollment_summary, student_rows
from audit.models import AuditLog
from core.exceptions import DomainError
from core.models import School
from people.models import Student

MON = date(2026, 10, 5)      # Monday inside Term 1
TODAY = date(2026, 10, 11)   # Sunday, 6 days after MON


class Base(TestCase):
    def setUp(self):
        sync_role_groups()
        self.school = School.objects.create(name="S")
        self.year = AcademicYear.objects.create(
            school=self.school, name="Y", starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30), is_current=True)
        self.term = Term.objects.create(academic_year=self.year, name="Term 1", starts_on=date(2026, 9, 1), ends_on=date(2026, 12, 15))
        grade = GradeLevel.objects.create(school=self.school, name="Grade 3", sort_order=3)
        grade4 = GradeLevel.objects.create(school=self.school, name="Grade 4", sort_order=4)
        self.teacher = User.objects.create_user("t1", password="x-long-test-password-1", role=Role.TEACHER, school=self.school)
        self.other_teacher = User.objects.create_user("t2", password="x-long-test-password-1", role=Role.TEACHER, school=self.school)
        self.registrar = User.objects.create_user("r1", password="x-long-test-password-1", role=Role.REGISTRAR, school=self.school)
        self.section = ClassSection.objects.create(academic_year=self.year, grade_level=grade, homeroom_teacher=self.teacher)
        self.other_section = ClassSection.objects.create(academic_year=self.year, grade_level=grade4, homeroom_teacher=self.other_teacher)
        self.enrollments = []
        for i, name in enumerate(["Abdi", "Bilal", "Cali"]):
            s = Student.objects.create(school=self.school, first_name=name, last_name="T", gender="male", date_of_birth=date(2017, 1, 1))
            self.enrollments.append(Enrollment.enroll(s, self.section, on=date(2026, 9, 1)))

    def take(self, day=MON, marks=None, actor=None, today=TODAY, section=None):
        return AttendanceRegister.take(section or self.section, day, marks or {}, actor or self.teacher, today=today)


class TakingAttendanceTests(Base):
    def test_everyone_defaults_to_present(self):
        register = self.take()
        self.assertEqual(register.counts(), {"present": 3, "absent": 0, "late": 0, "excused": 0})
        self.assertEqual(register.records.count(), 3)

    def test_exceptions_are_recorded_with_reasons(self):
        e = self.enrollments
        register = self.take(marks={e[0].pk: ("absent", "Sick"), e[1].pk: ("late", "Bus"), e[2].pk: ("present", "ignored")})
        self.assertEqual(register.counts(), {"present": 1, "absent": 1, "late": 1, "excused": 0})
        self.assertEqual(register.records.get(enrollment=e[0]).reason, "Sick")
        self.assertEqual(register.records.get(enrollment=e[2]).reason, "")  # present has no reason

    def test_saving_again_updates_instead_of_duplicating(self):
        e = self.enrollments
        self.take(marks={e[0].pk: ("absent", "")})
        register = self.take(marks={e[0].pk: ("present", ""), e[1].pk: ("excused", "Appointment")})
        self.assertEqual(AttendanceRegister.objects.count(), 1)
        self.assertEqual(register.records.count(), 3)
        self.assertEqual(register.counts(), {"present": 2, "absent": 0, "late": 0, "excused": 1})

    def test_audit_log(self):
        self.take()
        self.take()
        actions = list(AuditLog.objects.values_list("action", flat=True))
        self.assertEqual(actions.count("attendance_taken"), 1)
        self.assertEqual(actions.count("attendance_edited"), 1)

    def test_unknown_student_rejected(self):
        outsider = Student.objects.create(school=self.school, first_name="O", last_name="X", gender="male", date_of_birth=date(2017, 1, 1))
        other = Enrollment.enroll(outsider, self.other_section, on=date(2026, 9, 1))
        with self.assertRaisesMessage(DomainError, "do not belong"):
            self.take(marks={other.pk: ("absent", "")})

    def test_invalid_status_rejected_and_nothing_saved(self):
        with self.assertRaises(DomainError):
            self.take(marks={self.enrollments[0].pk: ("sleeping", "")})
        self.assertEqual(AttendanceRegister.objects.count(), 0)

    def test_student_who_joined_later_or_left_earlier_is_excluded(self):
        late_student = Student.objects.create(school=self.school, first_name="New", last_name="T", gender="male", date_of_birth=date(2017, 1, 1))
        Enrollment.enroll(late_student, self.section, on=date(2026, 10, 10))
        self.assertEqual(self.take(day=MON).records.count(), 3)
        self.assertEqual(len(self.section.enrollments_on(date(2026, 10, 12))), 4)
        leaver = self.enrollments[2]
        leaver.withdraw(on=date(2026, 10, 7))
        # Cali left on the 7th, the new student only joins on the 10th: just two students on the 8th.
        self.assertEqual(self.take(day=date(2026, 10, 8)).records.count(), 2)


class DayRuleTests(Base):
    def test_future_date_rejected(self):
        with self.assertRaisesMessage(DomainError, "future"):
            self.take(day=TODAY + timedelta(days=1))

    def test_friday_is_non_teaching_by_default(self):
        with self.assertRaisesMessage(DomainError, "non-teaching"):
            self.take(day=date(2026, 10, 9))  # Friday

    @override_settings(SCHOOL_NON_TEACHING_WEEKDAYS=[5, 6])
    def test_weekend_days_are_configurable(self):
        self.take(day=date(2026, 10, 9))  # Friday now allowed
        with self.assertRaises(DomainError):
            self.take(day=date(2026, 10, 10))  # Saturday blocked

    def test_holiday_blocks_attendance(self):
        Holiday.objects.create(school=self.school, date=MON, name="Independence Day")
        with self.assertRaisesMessage(DomainError, "Independence Day"):
            self.take()

    def test_outside_any_term_rejected(self):
        with self.assertRaisesMessage(DomainError, "not inside any term"):
            self.take(day=date(2026, 12, 21), today=date(2026, 12, 22))

    def test_locked_term_blocks_everyone(self):
        self.term.lock()
        with self.assertRaisesMessage(DomainError, "locked"):
            self.take(actor=self.registrar)

    def test_teacher_cannot_take_other_class(self):
        with self.assertRaisesMessage(DomainError, "not assigned"):
            self.take(actor=self.other_teacher)

    def test_teacher_edit_window(self):
        old = date(2026, 10, 3)  # Saturday, 8 days before TODAY: outside the 7-day window but inside the term
        with self.assertRaisesMessage(DomainError, "days back"):
            self.take(day=old, actor=self.teacher)
        self.take(day=old, actor=self.registrar)  # managers have no limit

    def test_registrar_can_take_any_class_in_school(self):
        self.take(actor=self.registrar, section=self.other_section)

    def test_day_problem_helper(self):
        self.assertIsNone(AttendanceRegister.day_problem(self.section, MON, today=TODAY))
        self.assertIn("future", AttendanceRegister.day_problem(self.section, TODAY + timedelta(days=1), today=TODAY))


class AccessTests(Base):
    def test_accessible_sections(self):
        self.assertEqual(list(self.teacher.accessible_sections()), [self.section])
        self.assertEqual(set(self.registrar.accessible_sections()), {self.section, self.other_section})
        other_school = School.objects.create(name="Other")
        outsider = User.objects.create_user("o", password="x-long-test-password-1", role=Role.REGISTRAR, school=other_school)
        self.assertEqual(list(outsider.accessible_sections()), [])


class ReportTests(Base):
    def setUp(self):
        super().setUp()
        e = self.enrollments
        for d, a in [(date(2026, 10, 5), "absent"), (date(2026, 10, 6), "absent"), (date(2026, 10, 7), "late"),
                     (date(2026, 10, 8), "present"), (date(2026, 10, 11), "excused")]:
            self.take(day=d, today=date(2026, 10, 12), marks={e[0].pk: (a, ""), e[1].pk: ("present", "")}, actor=self.registrar)

    def test_summary_and_rate(self):
        s = enrollment_summary(self.enrollments[0])
        self.assertEqual((s.present, s.absent, s.late, s.excused, s.days), (1, 2, 1, 1, 5))
        self.assertEqual(s.rate, 40.0)  # (1 present + 1 late) / 5
        self.assertEqual(enrollment_summary(self.enrollments[1]).rate, 100.0)

    def test_date_range_filter(self):
        s = enrollment_summary(self.enrollments[0], date(2026, 10, 7), date(2026, 10, 8))
        self.assertEqual((s.late, s.present, s.days), (1, 1, 2))

    def test_student_rows(self):
        rows = student_rows(self.section)
        self.assertEqual([r["student"].first_name for r in rows], ["Abdi", "Bilal", "Cali"])
        self.assertEqual(rows[2]["summary"].rate, 100.0)  # Cali never marked absent: present on all 5 days

    def test_chronic_absentees(self):
        result = chronic_absentees(self.teacher.accessible_sections(), None, None, threshold=90, min_days=5)
        self.assertEqual([r["student"].first_name for r in result], ["Abdi"])
        self.assertEqual(chronic_absentees(self.teacher.accessible_sections(), None, None, 90, min_days=6), [])

    def test_class_day_rows_show_taken_and_not_taken(self):
        rows = class_day_rows([self.section, self.other_section], date(2026, 10, 5))
        by_name = {r["section"].display_name: r for r in rows}
        self.assertIsNotNone(by_name["Grade 3"]["register"])
        self.assertEqual(by_name["Grade 3"]["summary"].absent, 1)
        self.assertIsNone(by_name["Grade 4"]["register"])
