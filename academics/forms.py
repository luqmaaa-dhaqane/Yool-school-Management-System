from django import forms

from people.models import Student

from .models import ClassSection


class PromoteForm(forms.Form):
    """Year-end promotion of one class."""

    target_section = forms.ModelChoiceField(
        queryset=ClassSection.objects.none(), label="Promote everyone to"
    )
    repeat_students = forms.ModelMultipleChoiceField(
        queryset=Student.objects.none(),
        required=False,
        label="Students who repeat the year",
        widget=forms.SelectMultiple(attrs={"size": 12}),
        help_text="Hold Ctrl (or Cmd) to select several. Leave empty if nobody repeats.",
    )
    repeat_section = forms.ModelChoiceField(
        queryset=ClassSection.objects.none(),
        required=False,
        label="Repeating students go to",
        help_text="Same grade, next academic year.",
    )

    def __init__(self, section, *args, **kwargs):
        super().__init__(*args, **kwargs)
        later = ClassSection.objects.filter(
            academic_year__school=section.academic_year.school,
            academic_year__starts_on__gt=section.academic_year.starts_on,
        ).select_related("grade_level", "academic_year")
        grade = section.grade_level.sort_order
        self.fields["target_section"].queryset = later.filter(grade_level__sort_order__gt=grade)
        self.fields["repeat_section"].queryset = later.filter(grade_level__sort_order=grade)
        self.fields["repeat_students"].queryset = Student.objects.filter(
            enrollments__class_section=section, enrollments__status="enrolled"
        ).order_by("last_name", "first_name")

    def clean(self):
        data = super().clean()
        if data.get("repeat_students") and not data.get("repeat_section"):
            self.add_error("repeat_section", "Choose a class for the students who repeat.")
        return data
