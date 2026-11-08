# SIS: Student Information System (Phase 1)

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
| `academics` | Years, terms, grades, sections, subjects, **`Enrollment`** (capacity, one class per year, withdraw, transfer, graduate, promote, repeat), year-end promotion, class roster |

## Using Phase 1

- **Place a student in a class:** Students > open a student > "Class enrollments", or People/Academics > Enrollments > Add.
- **Import a list:** Students > "Import from CSV". Download the template, fill it, preview, then save.
  Command line: `python manage.py import_students file.csv` (add `--commit` to save).
- **Class list:** Class sections > "View / print" (also CSV download).
- **Year end:** Class sections > tick one class > action "Promote this class to next year...". Pick the next class
  and anyone repeating. For a final-year class use "Graduate this class".
- **Leaving:** Enrollments > tick students > "Withdraw" / "Transferred out" / "Graduated".

## Upgrading from Phase 0

Copy the new files over your `sis` folder (keep `.venv`, `.env`, `db.sqlite3`), then:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed --school "<your exact school name>"   # adds classes and syncs permissions
```

## Conventions

1. Business rules go on the model or in `services.py`. Never in views or templates.
2. Break a rule by raising `DomainError`; the interface layer shows the message.
3. Change state only through methods (`withdraw()`, `lock()`), not by assigning fields.
4. Every new model gets a `school` FK (or reaches one) and joins `ROLE_PERMISSIONS`.
5. New model => migration committed in the same change (CI enforces this).

## Next (Phase 2)

Attendance: one-screen daily register per class, absence reports.

## Before going live

Set `DEBUG=False`, a long random `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
`SECURE_SSL_REDIRECT=True`, run behind HTTPS (Caddy), and set up nightly `pg_dump` backups.
