# Yool International School Student Management System (Phase 4 + new interface)

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
| `reports` | **Report cards (PDF)**: results per subject, position, attendance, class teacher and Principal comments; one student or the whole class |
| `grades` | **Weighted assessments** (quiz / midterm / exam), grade entry grid, term grades with letter bands, ranking, class mark sheet, term locking |
| `attendance` | **Daily class registers** (Present/Absent/Late/Excused, reasons), holidays, daily summary, attendance by student, low-attendance list, CSV exports |
| `academics` | Years, terms, grades, sections, subjects, **`Enrollment`** (capacity, one class per year, withdraw, transfer, graduate, promote, repeat), year-end promotion, class roster |

## Using the system

- **Place a student in a class:** Students > open a student > "Class enrollments", or Enrollments > Add.
- **Import a list:** Students > "Import from CSV". Download the template, fill it, preview, then save.
- **Class list:** Classes > "View / print" (also CSV).
- **Year end:** Classes > tick one class > "Promote this class to next year...".
- **Leaving:** Enrollments > tick students > "Withdraw" / "Transferred out" / "Graduated".
- **Take attendance:** Attendance > "Take attendance" next to the class. Everyone starts as Present; mark the
  exceptions (with an optional reason) and save. Open the same page again to correct it.
- **Reports:** Attendance > Daily summary / Attendance by student / Low attendance list (all have CSV and print).
- **Holidays:** Attendance > Holidays. Fridays are non-teaching by default (`SCHOOL_NON_TEACHING_WEEKDAYS` in `.env`).
- **Subjects and teachers:** Classes > open a class > "Subjects and teachers" (or use the action "Add all
  school subjects to selected classes", then choose each teacher). PE and Arabic teachers who are not a homeroom
  teacher see only the classes they teach and enter grades; they do not take attendance.
- **Grades:** Gradebook > pick the term > "Enter grades". Add an assessment first (Quiz 1, Midterm...). An empty
  box means "no score yet" and is not counted as zero. Term grade = average per type x type weight; missing types
  (for example no exam yet) are left out and the other weights rescaled. Letter bands: Grade bands.
- **Lock a term:** Terms > tick the term > "Lock selected terms". Locked terms freeze grades and attendance.
- **Mark sheet:** Gradebook > Class mark sheet (all subjects, overall average, rank, CSV).
- **Classes:** a grade is one class ("Grade 1", "Grade 2", ...). There are no A/B sections; each grade has exactly
  one class per academic year. CSV imports use the `grade` column only (an old `section` column is ignored).
- **Report cards:** Report cards > pick the term > "Comments" to write the class teacher's and the Principal's comments,
  then "Class PDF" for the whole class (or "PDF" beside one student on the Comments page). Until the term is locked
  every card carries a PROVISIONAL warning; lock the term first for the final copies. The class teacher writes
  the teacher comment while the term is open; only the Principal writes the Principal's comment.
- **Teachers** only see their own class (the class where they are set as *Homeroom teacher*), its students,
  guardians and registers. They can correct attendance up to 7 days back (`ATTENDANCE_EDIT_DAYS`);
  Principal and Registrar have no limit. A locked term cannot be changed by anyone.

## Updating to a new version

Back up `db.sqlite3`, copy the new files over your `sis` folder (keep `.venv`, `.env`, `db.sqlite3`), then:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py finish_update    # refreshes permissions and adds any missing set-up; needs no school name
```

## Conventions

1. Business rules go on the model or in `services.py`. Never in views or templates.
2. Break a rule by raising `DomainError`; the interface layer shows the message.
3. Change state only through methods (`withdraw()`, `lock()`), not by assigning fields.
4. Every new model gets a `school` FK (or reaches one) and joins `ROLE_PERMISSIONS`.
5. New model => migration committed in the same change (CI enforces this).

## Interface

- **Look:** the school's colours (brown, green, gold) and logo. All styling is in `core/static/core/theme.css`;
  templates live in `templates/admin/` (shell, login, home) and in each app's `templates/admin/<app>/`.
- **Sidebar:** Overview, Students, Classes, Attendance, Gradebook, Report cards, Settings. Each person sees only the
  sections they may open (`core/context_processors.py`). "Settings" opens the list of all records on the home page.
- **Home page:** a dashboard that depends on who you are (`core/dashboard.py`): class teachers see today's register and
  what is not taken yet; subject teachers see their subjects; the Principal also gets term and low-attendance reminders.
- **Logo:** `core/static/core/logo.jpg` (the tree emblem and YOOL lettering, shown on a white tile and on report cards).
  Replace the file to change it. Name and subtitle come from `SITE_NAME`, `SITE_SUBTITLE`, `SITE_SHORT_NAME` in `.env`.
- **Light theme only:** the dark-mode switch was removed so every screen matches the design.
- **Menu:** stays in place while you scroll and can be collapsed to icons (the arrows at its top; the choice is remembered).
  The search box in the header finds a student by name or number.
- **Settings:** its own section (School, Academic, Grading, Attendance, Admins, Activity) with tabs and a button for every
  action (`core/settings_registry.py` lists what is in it). Staff accounts live under **Admins**.
- **Gradebook:** "Add assessment" and "Edit" open a dialog on the page (no separate window). An assessment with scores
  cannot be deleted. Positions are called **ranks**.
- **Help:** short role-aware guides at `/admin/help/`.
- **Safety nets:** a second click on Save is ignored; leaving attendance, grades or comments with unsaved changes asks first.
- **Locked accounts** see a branded "too many sign-in attempts" page.

## Security

- Login lockout: 5 wrong passwords for the same username and address lock it for 15 minutes
  (`LOGIN_FAILURE_LIMIT`, `LOGIN_LOCKOUT_MINUTES`). Unlock early: `python manage.py axes_reset`.
- Nobody can delete their own account. The last active super admin cannot be deleted, deactivated or demoted.
  Users are deactivated, never bulk-deleted. On your own account the role and status fields are locked.
- "Super admin" is the role; the superuser flag always follows it.

## Branding

Name, subtitle and tab title come from `SITE_NAME`, `SITE_SUBTITLE`, `SITE_SHORT_NAME` in `.env`.
Put the logo at `core/static/core/logo.jpg`. A Show/Hide button appears on every password box.

## Next (Phase 5)

Launch: server, HTTPS, Postgres, backups, monitoring, email, CI/CD, pilot and training.

## Before going live

Set `DEBUG=False`, a long random `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
`SECURE_SSL_REDIRECT=True`, run behind HTTPS (Caddy), and set up nightly `pg_dump` backups.
