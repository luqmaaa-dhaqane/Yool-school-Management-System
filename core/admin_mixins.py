class SchoolScopedAdminMixin:
    """Scopes an admin to the logged-in user's school.

    * Non-superusers only see rows of their own school.
    * New rows default to the user's school.
    Set ``school_lookup`` to the ORM path of the school, e.g.
    ``"academic_year__school"``.
    """

    school_lookup = "school"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = request.user
        if not user.is_superuser and getattr(user, "school_id", None):
            qs = qs.filter(**{self.school_lookup: user.school_id})
        if not user.is_superuser and getattr(user, "role", "") == "teacher":
            qs = self.scope_for_teacher(qs, user)
        return qs

    def scope_for_teacher(self, qs, user):
        """Override to limit what a teacher sees (default: no extra limit)."""
        return qs

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if getattr(request.user, "school_id", None) and self.school_lookup == "school":
            initial.setdefault("school", request.user.school_id)
        return initial

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if (
            db_field.name == "school"
            and not request.user.is_superuser
            and getattr(request.user, "school_id", None)
        ):
            from core.models import School

            kwargs["queryset"] = School.objects.filter(pk=request.user.school_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
