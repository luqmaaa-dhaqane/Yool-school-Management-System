from datetime import date

from django.test import TestCase

from academics.models import AcademicYear, ClassSection, Enrollment, GradeLevel
from core.models import School
from people.importers import StudentImporter
from people.models import Guardian, Student

HEADER = "first_name,last_name,gender,date_of_birth,grade,section,guardian_name,guardian_phone,guardian_relationship\n"


class ImporterTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="S")
        self.year = AcademicYear.objects.create(
            school=self.school, name="Y", starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30), is_current=True)
        grade = GradeLevel.objects.create(school=self.school, name="Grade 3", sort_order=3)
        self.section = ClassSection.objects.create(academic_year=self.year, grade_level=grade, name="A", capacity=2)

    def run_csv(self, body, dry_run=False, header=HEADER):
        return StudentImporter(self.school).run((header + body).encode("utf-8"), dry_run=dry_run)

    def test_imports_students_guardians_and_enrolls(self):
        report = self.run_csv(
            "Ahmed,Hassan,male,2017-03-14,Grade 3,A,Hodan Ali,0630000000,Mother\n"
            "Sagal,Hassan,F,14/03/2018,3,A,Hodan Ali,0630000000,Mother\n")
        self.assertTrue(report.ok, report.errors)
        self.assertEqual((report.created, report.enrolled, report.guardians_created), (2, 2, 1))
        self.assertTrue(report.committed)
        self.assertEqual(Student.objects.count(), 2)
        self.assertEqual(Guardian.objects.count(), 1)  # siblings share one guardian
        self.assertEqual(self.section.enrolled_count(), 2)

    def test_preview_saves_nothing(self):
        report = self.run_csv("Ahmed,Hassan,male,2017-03-14,,,,,\n", dry_run=True)
        self.assertEqual(report.created, 1)
        self.assertFalse(report.committed)
        self.assertEqual(Student.objects.count(), 0)

    def test_any_error_means_nothing_saved_and_rows_are_reported(self):
        report = self.run_csv(
            "Good,One,male,2017-01-01,,,,,\n"
            "Bad,Date,male,not-a-date,,,,,\n"
            "Bad,Gender,robot,2017-01-01,,,,,\n"
            "Bad,Class,male,2017-01-01,Grade 9,A,,,\n")
        self.assertEqual([line for line, _ in report.errors], [3, 4, 5])
        self.assertFalse(report.committed)
        self.assertEqual(Student.objects.count(), 0)

    def test_duplicates_detected(self):
        self.run_csv("Ahmed,Hassan,male,2017-03-14,,,,,\n")
        report = self.run_csv("ahmed,HASSAN,male,2017-03-14,,,,,\n")
        self.assertIn("duplicate", report.errors[0][1].lower())

    def test_class_capacity_respected(self):
        rows = "".join(f"S{i},T,male,2017-01-0{i},Grade 3,A,,,\n" for i in (1, 2, 3))
        report = self.run_csv(rows)
        self.assertEqual(len(report.errors), 1)
        self.assertIn("full", report.errors[0][1])
        self.assertEqual(Student.objects.count(), 0)

    def test_missing_required_column(self):
        report = StudentImporter(self.school).run(b"first_name,last_name\nA,B\n")
        self.assertIn("date_of_birth", report.errors[0][1])

    def test_guardian_needs_name_and_phone_together(self):
        report = self.run_csv("A,B,male,2017-01-01,,,Only Name,,\n")
        self.assertEqual(len(report.errors), 1)

    def test_semicolon_bom_and_header_aliases(self):
        text = "\ufeffFirstName;Surname;Sex;DOB\nAli;Omar;lab;2017-05-05\n"
        report = StudentImporter(self.school).run(text.encode("utf-8"), dry_run=False)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(Student.objects.get().gender, "male")

    def test_blank_lines_ignored(self):
        report = self.run_csv("\nAli,Omar,male,2017-05-05,,,,,\n\n")
        self.assertEqual(report.total, 1)
