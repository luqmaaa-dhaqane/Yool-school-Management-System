import random
import secrets
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from academics.models import AcademicYear, ClassSection, ClassSubject, Enrollment, GradeLevel, Subject, Term
from accounts.models import Role, User
from accounts.permissions import sync_role_groups
from core.models import School
from grades.models import Assessment, AssessmentType
from grades.setup import ensure_grading_setup
from people.models import Guardian, Student

DEFAULT_SUBJECTS = [
    ("ENG", "English", "Af-Ingiriis"),
    ("SOM", "Somali", "Af-Soomaali"),
    ("ARB", "Arabic", "Carabi"),
    ("MTH", "Mathematics", "Xisaab"),
    ("SCI", "Science", "Sayniska"),
    ("SST", "Social Studies", "Daraasaad bulsho"),
    ("ISL", "Islamic Studies", "Diinta Islaamka"),
    ("PE", "Physical Education", "Tacliinta jirka"),
    ("ART", "Art", "Farshaxanka"),
    ("ICT", "Computer Studies", "Kombuyuutarka"),
]

DEMO_STUDENTS = [
    ("Ahmed", "Demo", "male"), ("Hodan", "Demo", "female"), ("Abdi", "Demo", "male"),
    ("Sagal", "Demo", "female"), ("Yusuf", "Demo", "male"),
]


class Command(BaseCommand):
    help = "Create baseline data: school, roles, academic year, grades, subjects, admin user."

    def add_arguments(self, parser):
        parser.add_argument("--school", default="My School")
        parser.add_argument("--admin-user", default="admin")
        parser.add_argument("--admin-password", default=None, help="Random if omitted.")
        parser.add_argument("--grades", type=int, default=12, help="Number of grade levels.")
        parser.add_argument("--demo", action="store_true", help="Also create fake demo students.")

    @transaction.atomic
    def handle(self, *args, **opts):
        sync_role_groups()
        self.stdout.write("Roles and permissions synced.")

        school, _ = School.objects.get_or_create(name=opts["school"])

        today = date.today()
        start_year = today.year if today.month >= 8 else today.year - 1
        year, created = AcademicYear.objects.get_or_create(
            school=school,
            name=f"{start_year}/{start_year + 1}",
            defaults={
                "starts_on": date(start_year, 9, 1),
                "ends_on": date(start_year + 1, 6, 30),
                "is_current": not AcademicYear.objects.filter(school=school, is_current=True).exists(),
            },
        )
        if created:
            terms = [
                ("Term 1", date(start_year, 9, 1), date(start_year, 12, 15)),
                ("Term 2", date(start_year + 1, 1, 5), date(start_year + 1, 3, 31)),
                ("Term 3", date(start_year + 1, 4, 10), date(start_year + 1, 6, 30)),
            ]
            for name, s, e in terms:
                Term.objects.create(academic_year=year, name=name, starts_on=s, ends_on=e)

        for n in range(1, opts["grades"] + 1):
            GradeLevel.objects.get_or_create(school=school, name=f"Grade {n}", defaults={"sort_order": n})

        current_year = AcademicYear.objects.filter(school=school, is_current=True).first() or year
        for grade in GradeLevel.objects.filter(school=school):
            ClassSection.objects.get_or_create(academic_year=current_year, grade_level=grade)

        for code, name, name_so in DEFAULT_SUBJECTS:
            Subject.objects.get_or_create(school=school, code=code, defaults={"name": name, "name_so": name_so})

        # Grading set-up (weights, letter bands) and every subject on every class (teachers are set later).
        ensure_grading_setup(school)

        username = opts["admin_user"]
        if not User.objects.filter(username=username).exists():
            password = opts["admin_password"] or secrets.token_urlsafe(12)
            User.objects.create_superuser(username=username, password=password, school=school)
            self.stdout.write(self.style.WARNING(
                f"Created admin user '{username}' with password: {password}\n"
                "Save it now; it is not shown again. Change it after first login."
            ))
        else:
            self.stdout.write(f"Admin user '{username}' already exists; left unchanged.")

        if opts["demo"] and not Student.objects.filter(school=school).exists():
            demo_section = ClassSection.objects.filter(
                academic_year=current_year, grade_level__name="Grade 3"
            ).first()
            guardian = Guardian.objects.create(
                school=school, first_name="Demo", last_name="Guardian", phone="000000000"
            )
            for first, last, gender in DEMO_STUDENTS:
                student = Student.objects.create(
                    school=school, first_name=first, last_name=last, gender=gender,
                    date_of_birth=date(today.year - 8, 1, 1), admitted_on=current_year.starts_on,
                )
                student.add_guardian(guardian, "Parent", is_primary=True)
                Enrollment.enroll(student, demo_section, on=current_year.starts_on)  # enrolled from the start of the year
            self.stdout.write("Demo students created and placed in Grade 3.")

        if opts["demo"] and not User.objects.filter(username="teacher").exists():
            password = secrets.token_urlsafe(10)
            teacher = User.objects.create_user(
                username="teacher", password=password, role=Role.TEACHER, school=school,
                first_name="Demo", last_name="Teacher",
            )
            ClassSection.objects.filter(
                academic_year=current_year, grade_level__name="Grade 3"
            ).update(homeroom_teacher=teacher)
            ClassSubject.objects.filter(
                class_section__academic_year=current_year, class_section__grade_level__name="Grade 3",
                subject__code__in=["MTH", "ENG"],
            ).update(teacher=teacher)
            self.stdout.write(self.style.WARNING(
                f"Created demo teacher 'teacher' (Grade 3, teaches Mathematics and English) with password: {password}"
            ))

        if opts["demo"]:
            self._demo_grades(school, current_year)

        self.stdout.write(self.style.SUCCESS("Seed complete."))

    def _demo_grades(self, school, year):
        """A few sample assessments with scores so the gradebook has something to show."""
        cs = ClassSubject.objects.filter(
            class_section__academic_year=year, class_section__grade_level__name="Grade 3",
            subject__code="MTH",
        ).select_related("teacher", "class_section").first()
        today = date.today()
        term = year.term_on(today) or year.terms.filter(starts_on__lte=today).order_by("-starts_on").first()
        if not (cs and cs.teacher and term) or Assessment.objects.filter(class_subject=cs, term=term).exists():
            return
        rng = random.Random(7)
        quiz, midterm = (AssessmentType.objects.get(school=school, name=n) for n in ("Quiz", "Midterm"))
        enrollments = cs.class_section.enrollments_between(term.starts_on, term.ends_on)
        for title, kind, maximum, offset in [("Quiz 1", quiz, 20, 14), ("Quiz 2", quiz, 20, 7), ("Midterm", midterm, 50, 3)]:
            held = min(max(today - timedelta(days=offset), term.starts_on), term.ends_on)
            assessment = Assessment.objects.create(
                class_subject=cs, term=term, assessment_type=kind, title=title, max_score=maximum, held_on=held
            )
            scores = {e.pk: Decimal(rng.randint(int(maximum * 0.45), maximum)) for e in enrollments}
            assessment.record_scores(scores, cs.teacher)
        self.stdout.write("Demo grades created (Grade 3, Mathematics).")
