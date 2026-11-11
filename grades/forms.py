from django import forms

from academics.models import ClassSection, Term


class MarksheetForm(forms.Form):
    term = forms.ModelChoiceField(queryset=Term.objects.none(), label="Term")
    section = forms.ModelChoiceField(queryset=ClassSection.objects.none(), label="Class")

    def __init__(self, *args, terms, sections, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["term"].queryset = terms
        self.fields["section"].queryset = sections
