"""CSV import of students (and their guardian contact) with optional class placement."""
import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from core.exceptions import DomainError

TEMPLATE_HEADER = [
    "first_name", "middle_name", "last_name", "gender", "date_of_birth", "admitted_on",
    "previous_school", "grade", "section", "guardian_name", "guardian_phone", "guardian_relationship",
]
TEMPLATE_ROWS = [
    ["Ahmed", "", "Hassan", "male", "2017-03-14", "2026-09-01", "", "Grade 3", "A", "Hodan Ali", "0630000000", "Mother"],
    ["Sagal", "Omar", "Yusuf", "female", "2016-11-02", "", "Star Primary", "Grade 4", "A", "Yusuf Abdi", "0631111111", "Father"],
]
REQUIRED = ("first_name", "last_name", "gender", "date_of_birth")
ALIASES = {
    "firstname": "first_name", "first": "first_name", "lastname": "last_name", "surname": "last_name",
    "last": "last_name", "dob": "date_of_birth", "birth_date": "date_of_birth", "birthdate": "date_of_birth",
    "sex": "gender", "class": "grade", "parent_name": "guardian_name", "parent_phone": "guardian_phone",
    "guardian": "guardian_name", "phone": "guardian_phone", "relationship": "guardian_relationship",
}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")
MALE = {"m", "male", "boy", "lab"}
FEMALE = {"f", "female", "girl", "dhedig"}


@dataclass
class ImportReport:
    dry_run: bool
    total: int = 0
    created: int = 0
    enrolled: int = 0
    guardians_created: int = 0
    errors: list = field(default_factory=list)  # list of (row_number, message)
    committed: bool = False

    @property
    def ok(self):
        return not self.errors


def _norm_header(name):
    key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(key, key)


def _parse_date(value, label):
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{label} '{value}' is not a valid date (use YYYY-MM-DD or DD/MM/YYYY).")


def _parse_gender(value):
    v = (value or "").strip().lower()
    if v in MALE:
        return "male"
    if v in FEMALE:
        return "female"
    raise ValueError(f"Gender '{value}' must be male or female.")


class StudentImporter:
    def __init__(self, school, academic_year=None):
        from academics.models import AcademicYear

        self.school = school
        self.year = academic_year or AcademicYear.objects.filter(school=school, is_current=True).first()

    # --- public ----------------------------------------------------------
    def run(self, source, dry_run=True):
        report = ImportReport(dry_run=dry_run)
        text = self._read(source)
        rows, header_error = self._rows(text)
        if header_error:
            report.errors.append((1, header_error))
            return report
        with transaction.atomic():
            for line_no, row in rows:
                report.total += 1
                try:
                    with transaction.atomic():  # savepoint: a bad row does not poison the rest
                        self._import_row(row, report)
                except (ValidationError, DomainError, ValueError) as exc:
                    message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
                    report.errors.append((line_no, message))
            if dry_run or report.errors:
                transaction.set_rollback(True)  # preview, or all-or-nothing on any error
            else:
                report.committed = True
        return report

    # --- parsing ---------------------------------------------------------
    @staticmethod
    def _read(source):
        raw = source.read() if hasattr(source, "read") else source
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
        return raw.lstrip("\ufeff")

    @staticmethod
    def _rows(text):
        first = text.splitlines()[0] if text.strip() else ""
        delimiter = ";" if first.count(";") > first.count(",") else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        try:
            header = [_norm_header(h) for h in next(reader)]
        except StopIteration:
            return [], "The file is empty."
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            return [], "Missing required column(s): " + ", ".join(missing)
        rows = []
        for values in reader:
            if not any(v.strip() for v in values):
                continue
            row = {h: (values[i].strip() if i < len(values) else "") for i, h in enumerate(header)}
            rows.append((reader.line_num, row))
        return rows, None

    # --- one row ---------------------------------------------------------
    def _import_row(self, row, report):
        from academics.models import ClassSection, Enrollment
        from people.models import Guardian, Student

        for column in REQUIRED:
            if not row.get(column):
                raise ValueError(f"'{column}' is empty.")
        first, last = row["first_name"], row["last_name"]
        dob = _parse_date(row["date_of_birth"], "Date of birth")
        twin = Student.objects.filter(
            school=self.school, first_name__iexact=first, last_name__iexact=last, date_of_birth=dob
        ).first()
        if twin:
            raise ValueError(f"Possible duplicate of {twin.student_no} {twin.full_name}.")

        section = None
        grade_text, section_text = row.get("grade", ""), row.get("section", "")
        if grade_text:
            section = self._find_section(grade_text, section_text, ClassSection)

        student = Student(
            school=self.school, first_name=first, middle_name=row.get("middle_name", ""), last_name=last,
            gender=_parse_gender(row["gender"]), date_of_birth=dob,
            admitted_on=_parse_date(row["admitted_on"], "Admission date") if row.get("admitted_on") else None,
            previous_school=row.get("previous_school", ""),
        )
        student.full_clean()
        student.save()
        report.created += 1

        guardian_name, phone = row.get("guardian_name", ""), row.get("guardian_phone", "")
        if guardian_name or phone:
            if not (guardian_name and phone):
                raise ValueError("Guardian name and phone must be given together.")
            guardian = Guardian.objects.filter(school=self.school, phone=phone).first()
            if guardian is None:
                parts = guardian_name.split(None, 1)
                guardian = Guardian.objects.create(
                    school=self.school, first_name=parts[0], last_name=parts[1] if len(parts) > 1 else "", phone=phone
                )
                report.guardians_created += 1
            student.add_guardian(guardian, row.get("guardian_relationship") or "Guardian", is_primary=True)

        if section:
            # Existing students count as enrolled from the admission date or the year start, whichever is later.
            start = section.academic_year.starts_on
            enrolled_on = max(student.admitted_on, start) if student.admitted_on else start
            Enrollment.enroll(student, section, on=enrolled_on)
            report.enrolled += 1

    def _find_section(self, grade_text, section_text, ClassSection):
        if self.year is None:
            raise ValueError("The school has no current academic year; cannot place students in classes.")
        grade = grade_text.strip()
        if grade.isdigit():
            grade = f"Grade {grade}"
        sections = ClassSection.objects.filter(
            academic_year=self.year, grade_level__name__iexact=grade
        ).select_related("grade_level")
        if section_text:
            sections = sections.filter(name__iexact=section_text.strip())
        found = list(sections[:2])
        if not found:
            label = f"{grade} {section_text}".strip()
            raise ValueError(f"No class '{label}' exists in {self.year}.")
        if len(found) > 1:
            raise ValueError(f"'{grade}' has several sections; add a 'section' value.")
        return found[0]
