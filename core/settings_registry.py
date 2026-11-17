"""What lives under Settings, grouped into sections. Each item opens an existing admin list."""
from django.urls import reverse

# (key, label, description, [(title, description, app, model, allow_add)])
SECTIONS = [
    ("school", "School", "Your school's profile.",
     [("School profile", "Name, contact details and currency.", "core", "school", False)]),
    ("academic", "Academic", "Years, terms, grades and subjects.",
     [("Academic years", "The school years. One is current.", "academics", "academicyear", True),
      ("Terms", "Term dates. Lock a term to freeze its grades and attendance.", "academics", "term", True),
      ("Grade levels", "The grades your school teaches.", "academics", "gradelevel", True),
      ("Subjects", "Subjects taught, with Somali names.", "academics", "subject", True)]),
    ("grading", "Grading", "How term grades are worked out.",
     [("Assessment types", "Quiz, midterm, exam and how much each counts.", "grades", "assessmenttype", True),
      ("Grade bands", "Letter grades and the percentage that earns each.", "grades", "gradeband", True)]),
    ("attendance", "Attendance", "Days the school is closed.",
     [("Holidays", "Attendance cannot be taken on these days.", "attendance", "holiday", True)]),
    ("admins", "Admins", "Who can sign in and what they can do.",
     [("Admins and staff", "Principals, registrars and teachers. Deactivate an account instead of deleting it.", "accounts", "user", True)]),
    ("activity", "Activity", "Who changed what, and when.",
     [("Audit log", "A read-only record of every change.", "audit", "auditlog", False)]),
]


def visible_sections(user):
    """Sections and items the user may open, each with ready-to-use URLs."""
    result = []
    for key, label, description, items in SECTIONS:
        shown = []
        for title, text, app, model, allow_add in items:
            if not user.has_perm(f"{app}.view_{model}"):
                continue
            can_add = allow_add and user.has_perm(f"{app}.add_{model}")
            shown.append({
                "title": title, "description": text, "prefix": f"/admin/{app}/{model}/",
                "url": reverse(f"admin:{app}_{model}_changelist"),
                "add_url": reverse(f"admin:{app}_{model}_add") if can_add else "",
            })
        if shown:
            result.append({"key": key, "label": label, "description": description, "items": shown})
    return result
