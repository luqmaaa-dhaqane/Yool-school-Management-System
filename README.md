# SIS: Student Information System (Phase 2)

Django + PostgreSQL. Object-oriented domain design: business rules live on the
model classes (`Student.withdraw()`, `AcademicYear.lock()`), views stay thin.

## Quick start (no Docker, uses SQLite)

```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # Windows: copy .env.example .env
python manage.py migrate
python manage.py seed --school "My School" --demo        # prints the admin password once
python manage.py runserver
```
Open http://127.0.0.1:8000/admin/ and log in as `admin`.

## With PostgreSQL (recommended)

```bash
docker compose up -d db
# in .env, uncomment: DATABASE_URL=postgres://sis:sis_dev_password@localhost:5432/sis
python manage.py migrate && python manage.py seed --school "My School"
```

## Tests

```bash
python manage.py test
```

## Scope

Students, classes, enrollment, attendance, grades and report cards. No parent portal, no fees.
Roles: Super Admin, Principal, Registrar, Teacher. Guardians are plain contact records (no login).

## What is built

| Area | Contents |
|---|---|
| `core` | Base classes (UUID, soft delete), `School`, `DomainError`, school-scoped admin mixin, `/health/` |
| `accounts` | `User` + `Role`; one permission group per role (`permissions.py`) |
| `audit` | Append-only audit log of admin changes, promotions, imports |
| `people` | `Student` (ID `YYYY-NNNN`, status rules), `Guardian` contacts, **CSV import** |
| `attendance` | **Daily class registers** (Present/Absent/Late/Excused, reasons), holidays, daily summary, attendance by student, low-attendance list, CSV exports |
| `academics` | Years, terms, grades, sections, subjects, **`Enrollment`** (capacity, one class per year, withdraw, transfer, graduate, promote, repeat), year-end promotion, class roster |

## Using the system

- **Place a student in a class:** Students > open a student > "Class enrollments", or Enrollments > Add.
- **Import a list:** Students > "Import from CSV". Download the template, fill it, preview, then save.
- **Class list:** Class sections > "View / print" (also CSV).
- **Year end:** Class sections > tick one class > "Promote this class to next year...".
- **Leaving:** Enrollments > tick students > "Withdraw" / "Transferred out" / "Graduated".
- **Take attendance:** Attendance > "Take attendance" next to the class. Everyone starts as Present; mark the
  exceptions (with an optional reason) and save. Open the same page again to correct it.
- **Reports:** Attendance > Daily summary / Attendance by student / Low attendance list (all have CSV and print).
- **Holidays:** Attendance > Holidays. Fridays are non-teaching by default (`SCHOOL_NON_TEACHING_WEEKDAYS` in `.env`).
- **Teachers** only see their own class (the class where they are set as *Homeroom teacher*), its students,
  guardians and registers. They can correct attendance up to 7 days back (`ATTENDANCE_EDIT_DAYS`);
  Principal and Registrar have no limit. A locked term cannot be changed by anyone.

## Updating to a new version

Back up `db.sqlite3`, copy the new files over your `sis` folder (keep `.venv`, `.env`, `db.sqlite3`), then:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py sync_roles        # refreshes permissions; needs no school name
```

## Conventions

1. Business rules go on the model or in `services.py`. Never in views or templates.
2. Break a rule by raising `DomainError`; the interface layer shows the message.
3. Change state only through methods (`withdraw()`, `lock()`), not by assigning fields.
4. Every new model gets a `school` FK (or reaches one) and joins `ROLE_PERMISSIONS`.
5. New model => migration committed in the same change (CI enforces this).

## Next (Phase 3)

Grades: subject teachers per class, assessments with weights, grade entry, term grades, term locking.

## Before going live

Set `DEBUG=False`, a long random `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
`SECURE_SSL_REDIRECT=True`, run behind HTTPS (Caddy), and set up nightly `pg_dump` backups.
