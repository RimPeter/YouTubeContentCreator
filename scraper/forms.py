"""
Forms for project and transcript management.
"""

from django import forms
from .models import VideoProject, SourceVideo


class VideoProjectForm(forms.ModelForm):
    """Form for creating and editing VideoProject instances."""
    
    class Meta:
        model = VideoProject
        fields = ['title', 'description', 'expected_duration', 'status']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter project title',
                'required': True,
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Project notes and description',
                'rows': 4,
            }),
            'expected_duration': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Duration in seconds (optional)',
                'min': 0,
            }),
            'status': forms.Select(attrs={
                'class': 'form-control',
            }),
        }


class SourceVideoForm(forms.ModelForm):
    """Form for creating and editing SourceVideo instances."""
    
    class Meta:
        model = SourceVideo
        fields = ['youtube_url', 'youtube_video_id', 'title', 'channel', 'duration', 'thumbnail_url']
        widgets = {
            'youtube_url': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://www.youtube.com/watch?v=...',
                'required': True,
            }),
            'youtube_video_id': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Auto-populated (read-only)',
                'readonly': True,
            }),
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Video title',
                'required': True,
            }),
            'channel': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Channel name',
                'required': True,
            }),
            'duration': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Duration in seconds',
                'min': 0,
            }),
            'thumbnail_url': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'Thumbnail image URL (optional)',
            }),
        }


class QuickCreateProjectForm(forms.Form):
    """Simple form for quickly creating a project with a YouTube URL."""
    
    project_title = forms.CharField(
        max_length=255,
        label='Project Title',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'My Reaction Video Project',
            'required': True,
        })
    )
    youtube_url = forms.URLField(
        label='YouTube URL',
        widget=forms.URLInput(attrs={
            'class': 'form-control',
            'placeholder': 'https://www.youtube.com/watch?v=...',
            'required': True,
        })
    )
