from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from academics.models import AcademicYear, Term
from core.models import School


class AcademicYearTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="S")

    def make_year(self, name, **kw):
        data = dict(school=self.school, name=name, starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30))
        data.update(kw)
        return AcademicYear.objects.create(**data)

    def test_only_one_current_year(self):
        a = self.make_year("2026/27", is_current=True)
        b = self.make_year("2027/28", is_current=True)
        a.refresh_from_db()
        self.assertFalse(a.is_current)
        self.assertTrue(b.is_current)

    def test_make_current(self):
        a = self.make_year("A", is_current=True)
        b = self.make_year("B")
        b.make_current()
        a.refresh_from_db()
        self.assertFalse(a.is_current)
        self.assertTrue(AcademicYear.objects.get(pk=b.pk).is_current)

    def test_dates_validated(self):
        with self.assertRaises(ValidationError):
            AcademicYear(school=self.school, name="X", starts_on=date(2026, 9, 1), ends_on=date(2026, 8, 1)).full_clean()

    def test_locking_year_locks_terms(self):
        year = self.make_year("Y")
        term = Term.objects.create(academic_year=year, name="T1", starts_on=date(2026, 9, 1), ends_on=date(2026, 12, 15))
        self.assertTrue(term.is_open())
        year.lock()
        term.refresh_from_db()
        self.assertTrue(term.is_locked)
        self.assertFalse(term.is_open())

    def test_term_must_be_inside_year(self):
        year = self.make_year("Y")
        term = Term(academic_year=year, name="T", starts_on=date(2026, 1, 1), ends_on=date(2026, 3, 1))
        with self.assertRaises(ValidationError):
            term.full_clean()

    def test_term_contains(self):
        year = self.make_year("Y")
        term = Term.objects.create(academic_year=year, name="T1", starts_on=date(2026, 9, 1), ends_on=date(2026, 12, 15))
        self.assertTrue(term.contains(date(2026, 10, 1)))
        self.assertFalse(term.contains(date(2027, 1, 1)))
