from datetime import date

from django.test import TestCase

from core.models import School
from people.models import Student


class HealthTests(TestCase):
    def test_health_endpoint(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class SoftDeleteTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="S")
        self.student = Student.objects.create(
            school=self.school, first_name="A", last_name="B", gender="male",
            date_of_birth=date(2018, 1, 1),
        )

    def test_delete_is_soft(self):
        self.student.delete()
        self.assertFalse(Student.objects.filter(pk=self.student.pk).exists())
        self.assertTrue(Student.all_objects.filter(pk=self.student.pk).exists())

    def test_queryset_delete_is_soft(self):
        Student.objects.all().delete()
        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(Student.all_objects.count(), 1)

    def test_restore(self):
        self.student.delete()
        self.student.restore()
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())
