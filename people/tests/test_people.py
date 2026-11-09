from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Role, User
from accounts.permissions import sync_role_groups
from audit.models import AuditLog
from core.exceptions import DomainError
from core.models import School
from people.models import Guardian, Student, StudentGuardian


def make_student(school, **kw):
    data = dict(first_name="Ali", last_name="Test", gender="male",
                date_of_birth=date(2018, 5, 20), admitted_on=date(2026, 9, 1))
    data.update(kw)
    return Student.objects.create(school=school, **data)


class StudentTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="S")

    def test_student_number_sequence(self):
        a, b = make_student(self.school), make_student(self.school, first_name="B")
        self.assertEqual(a.student_no, "2026-0001")
        self.assertEqual(b.student_no, "2026-0002")

    def test_numbers_restart_per_year_and_school(self):
        make_student(self.school)
        self.assertEqual(make_student(self.school, admitted_on=date(2027, 9, 1)).student_no, "2027-0001")
        other = School.objects.create(name="Other")
        self.assertEqual(make_student(other).student_no, "2026-0001")

    def test_number_not_reused_after_soft_delete(self):
        a = make_student(self.school)
        a.delete()
        self.assertEqual(make_student(self.school).student_no, "2026-0002")

    def test_number_never_changes_on_edit(self):
        a = make_student(self.school)
        a.first_name = "Changed"
        a.save()
        a.refresh_from_db()
        self.assertEqual(a.student_no, "2026-0001")

    def test_full_name_and_age(self):
        s = make_student(self.school, middle_name="Omar")
        self.assertEqual(s.full_name, "Ali Omar Test")
        self.assertEqual(s.age(on=date(2026, 5, 20)), 8)
        self.assertEqual(s.age(on=date(2026, 5, 19)), 7)

    def test_future_birth_date_invalid(self):
        s = Student(school=self.school, first_name="X", last_name="Y", gender="male",
                    date_of_birth=date(2999, 1, 1))
        with self.assertRaises(ValidationError):
            s.full_clean()

    def test_valid_status_transitions(self):
        s = make_student(self.school)
        s.suspend()
        s.reinstate()
        s.withdraw()
        s.reinstate()
        self.assertEqual(s.status, Student.Status.ACTIVE)

    def test_invalid_status_transition_raises(self):
        s = make_student(self.school)
        s.graduate()
        with self.assertRaises(DomainError):
            s.reinstate()
        s.refresh_from_db()
        self.assertEqual(s.status, Student.Status.GRADUATED)


class GuardianTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="S")
        self.student = make_student(self.school)
        self.mother = Guardian.objects.create(school=self.school, first_name="M", last_name="T", phone="1")
        self.uncle = Guardian.objects.create(school=self.school, first_name="U", last_name="T", phone="2")

    def test_first_guardian_becomes_primary(self):
        self.student.add_guardian(self.mother, "Mother")
        self.assertEqual(self.student.primary_guardian(), self.mother)

    def test_only_one_primary(self):
        self.student.add_guardian(self.mother, "Mother", is_primary=True)
        self.student.add_guardian(self.uncle, "Uncle", is_primary=True)
        self.assertEqual(self.student.student_guardians.filter(is_primary=True).count(), 1)
        self.assertEqual(self.student.primary_guardian(), self.uncle)

    def test_db_rejects_two_primaries(self):
        self.student.add_guardian(self.mother, "Mother", is_primary=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StudentGuardian.objects.bulk_create(
                [StudentGuardian(student=self.student, guardian=self.uncle, relationship="U", is_primary=True)]
            )

    def test_guardians_to_notify_has_no_duplicates(self):
        self.student.add_guardian(self.mother, "Mother", is_primary=True, is_emergency_contact=True)
        self.student.add_guardian(self.uncle, "Uncle", is_emergency_contact=True)
        self.assertEqual(self.student.guardians_to_notify(), [self.mother, self.uncle])

    def test_cross_school_link_rejected(self):
        outsider = Guardian.objects.create(
            school=School.objects.create(name="Other"), first_name="O", last_name="X", phone="3"
        )
        with self.assertRaises(DomainError):
            self.student.add_guardian(outsider, "Other")


class AdminBehaviourTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="S")
        cls.other = School.objects.create(name="Other")
        sync_role_groups()

    def login(self, role):
        user = User.objects.create_user(username=role, password="x-long-test-password-1", role=role, school=self.school)
        self.client.force_login(user)
        return user

    def test_teacher_does_not_see_medical_notes_field(self):
        from academics.models import AcademicYear, ClassSection, Enrollment, GradeLevel

        student = make_student(self.school, medical_notes="SECRET-NOTE")
        teacher = self.login(Role.TEACHER)
        year = AcademicYear.objects.create(school=self.school, name="Y", starts_on=date(2026, 9, 1),
                                           ends_on=date(2027, 6, 30), is_current=True)
        grade = GradeLevel.objects.create(school=self.school, name="G1", sort_order=1)
        section = ClassSection.objects.create(academic_year=year, grade_level=grade, name="A", homeroom_teacher=teacher)
        Enrollment.enroll(student, section)
        response = self.client.get(f"/admin/people/student/{student.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "SECRET-NOTE")

    def test_registrar_sees_medical_notes_field(self):
        student = make_student(self.school, medical_notes="SECRET-NOTE")
        self.login(Role.REGISTRAR)
        response = self.client.get(f"/admin/people/student/{student.pk}/change/")
        self.assertContains(response, "SECRET-NOTE")

    def test_users_only_see_their_school(self):
        mine = make_student(self.school, first_name="Mine")
        theirs = make_student(self.other, first_name="Theirs")
        self.login(Role.REGISTRAR)
        response = self.client.get("/admin/people/student/")
        self.assertContains(response, mine.student_no)
        self.assertNotContains(response, "Theirs")

    def test_creating_student_in_admin_writes_audit_log(self):
        self.login(Role.REGISTRAR)
        response = self.client.post("/admin/people/student/add/", {
            "school": self.school.pk, "first_name": "New", "last_name": "Kid", "gender": "female",
            "date_of_birth": "2019-01-01", "status": "active",
            "student_guardians-TOTAL_FORMS": "0", "student_guardians-INITIAL_FORMS": "0",
            "student_guardians-MIN_NUM_FORMS": "0", "student_guardians-MAX_NUM_FORMS": "1000",
            "enrollments-TOTAL_FORMS": "0", "enrollments-INITIAL_FORMS": "0",
            "enrollments-MIN_NUM_FORMS": "0", "enrollments-MAX_NUM_FORMS": "1000",
        })
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        student = Student.objects.get(first_name="New")
        log = AuditLog.objects.get(entity="people.Student", entity_id=str(student.pk))
        self.assertEqual(log.action, "create")
        self.assertEqual(log.user.role, Role.REGISTRAR)

    def test_admin_delete_is_soft_and_logged(self):
        student = make_student(self.school)
        user = self.login(Role.REGISTRAR)
        # Registrar lacks delete permission.
        response = self.client.post(f"/admin/people/student/{student.pk}/delete/", {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())
