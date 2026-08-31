from django import forms

from .models import VideoProject


class VideoProjectForm(forms.ModelForm):
    class Meta:
        model = VideoProject
        fields = ["title", "description", "expected_duration"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class TranscriptIngestionForm(forms.Form):
    youtube_url = forms.CharField(
        max_length=500,
        label="YouTube URL or video ID",
        widget=forms.TextInput(
            attrs={"placeholder": "https://www.youtube.com/watch?v=..."}
        ),
    )
