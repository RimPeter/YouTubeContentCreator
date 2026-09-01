from django import forms
from django.conf import settings

from .models import MediaAsset


class SourceMediaUploadForm(forms.Form):
    media_file = forms.FileField(
        help_text="Upload a local video you are authorized to use."
    )
    rights_basis = forms.ChoiceField(
        choices=[
            choice
            for choice in MediaAsset.RightsBasis.choices
            if choice[0] != MediaAsset.RightsBasis.UNKNOWN
        ]
    )
    rights_notes = forms.CharField(
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Record the ownership, licence, permission, or public-domain basis.",
    )
    rights_confirmed = forms.BooleanField(
        required=True,
        label="I confirm I am authorized to use this media in this project.",
    )


class SourceClipCreationForm(forms.Form):
    source_asset = forms.ModelChoiceField(queryset=MediaAsset.objects.none())
    padding_before_seconds = forms.DecimalField(
        min_value=0,
        max_value=settings.SOURCE_CLIP_MAX_PADDING_SECONDS,
        decimal_places=3,
        initial=0,
    )
    padding_after_seconds = forms.DecimalField(
        min_value=0,
        max_value=settings.SOURCE_CLIP_MAX_PADDING_SECONDS,
        decimal_places=3,
        initial=0,
    )

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_asset"].queryset = MediaAsset.objects.filter(
            project=project,
            kind=MediaAsset.Kind.SOURCE_UPLOAD,
            status__in=[MediaAsset.Status.VALIDATED, MediaAsset.Status.APPROVED],
            has_video=True,
        ).order_by("display_name", "-version")
