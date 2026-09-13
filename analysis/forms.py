from django import forms


class AnalysisConfigurationForm(forms.Form):
    fallback_max_chunks = forms.IntegerField(
        min_value=1,
        max_value=100,
        initial=20,
        help_text="Maximum transcript chunks per deterministic fallback segment.",
    )
    max_provider_attempts = forms.IntegerField(
        min_value=1,
        max_value=3,
        initial=2,
        help_text="Maximum structured-provider attempts before deterministic fallback.",
    )


class SegmentSelectionForm(forms.Form):
    reviewed_start_seconds = forms.FloatField(required=False, min_value=0)
    reviewed_end_seconds = forms.FloatField(required=False, min_value=0)
    notes = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("reviewed_start_seconds")
        end = cleaned.get("reviewed_end_seconds")
        if (start is None) != (end is None):
            raise forms.ValidationError("Provide both reviewed boundaries or neither.")
        if start is not None and end < start:
            raise forms.ValidationError("Reviewed end must not precede reviewed start.")
        return cleaned


class SegmentExclusionForm(forms.Form):
    reason = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Optional reason for excluding this suggestion.",
    )
