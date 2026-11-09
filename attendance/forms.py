from django import forms

from academics.models import ClassSection

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class PeriodForm(forms.Form):
    """Filters shared by the attendance reports."""

    section = forms.ModelChoiceField(queryset=ClassSection.objects.none(), required=False, label="Class")
    start = forms.DateField(required=False, widget=_DATE, input_formats=["%Y-%m-%d"], label="From")
    end = forms.DateField(required=False, widget=_DATE, input_formats=["%Y-%m-%d"], label="To")
    threshold = forms.FloatField(required=False, min_value=0, max_value=100, label="Below (% attendance)")
    min_days = forms.IntegerField(required=False, min_value=1, label="At least this many recorded days")

    def __init__(self, *args, sections=None, with_section=False, with_threshold=False, **kwargs):
        super().__init__(*args, **kwargs)
        if sections is not None:
            self.fields["section"].queryset = sections
        if not with_section:
            del self.fields["section"]
        if not with_threshold:
            del self.fields["threshold"], self.fields["min_days"]

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            self.add_error("end", "The end date must not be before the start date.")
        return data
