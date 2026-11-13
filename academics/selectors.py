"""Small shared queries about terms."""
from datetime import date

from .models import Term


def terms_for(user):
    """Terms the user may work with: their school's (everything for a super admin), newest first."""
    qs = Term.objects.select_related("academic_year").order_by("-starts_on")
    if user.is_superuser:
        return qs
    if not user.school_id:
        return qs.none()
    return qs.filter(academic_year__school_id=user.school_id)


def default_term(user):
    """The term running today, else the latest one that has started, else the first."""
    today = date.today()
    terms = list(terms_for(user))
    for term in terms:
        if term.contains(today):
            return term
    started = [t for t in terms if t.starts_on <= today]
    return started[0] if started else (terms[0] if terms else None)
