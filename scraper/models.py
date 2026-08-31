from django.db import models
from django.core.validators import URLValidator
from django.utils import timezone


class VideoProject(models.Model):
    """
    Represents one complete YouTube reaction-video production project.
    """
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('transcript_ready', 'Transcript Ready'),
        ('analysis_ready', 'Analysis Ready'),
        ('needs_review', 'Needs Review'),
        ('approved', 'Approved'),
        ('locked', 'Locked'),
        ('failed', 'Failed'),
    ]
    
    title = models.CharField(
        max_length=255,
        help_text="Title of the YouTube reaction video project"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        help_text="Current pipeline stage of this project"
    )
    current_pipeline_stage = models.CharField(
        max_length=100,
        default='created',
        help_text="Detailed description of current stage"
    )
    description = models.TextField(
        blank=True,
        help_text="Project notes and description"
    )
    expected_duration = models.IntegerField(
        null=True,
        blank=True,
        help_text="Expected video duration in seconds"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-updated_at']
        verbose_name = "Video Project"
        verbose_name_plural = "Video Projects"
    
    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


class SourceVideo(models.Model):
    """
    Represents the YouTube source video attached to a project.
    One VideoProject -> One SourceVideo (one-to-one relationship)
    """
    
    project = models.OneToOneField(
        VideoProject,
        on_delete=models.CASCADE,
        related_name='source_video',
        help_text="The project this source video belongs to"
    )
    youtube_url = models.URLField(
        help_text="Full URL of the YouTube video"
    )
    youtube_video_id = models.CharField(
        max_length=11,
        unique=True,
        help_text="YouTube video ID (extracted from URL)"
    )
    title = models.CharField(
        max_length=255,
        help_text="Title of the YouTube video"
    )
    channel = models.CharField(
        max_length=255,
        help_text="Channel name of the source video"
    )
    duration = models.IntegerField(
        null=True,
        blank=True,
        help_text="Duration in seconds"
    )
    thumbnail_url = models.URLField(
        blank=True,
        help_text="URL of the video thumbnail"
    )
    
    transcript_fetched = models.BooleanField(
        default=False,
        help_text="Whether transcript has been fetched"
    )
    transcript_status = models.CharField(
        max_length=50,
        default='pending',
        choices=[
            ('pending', 'Pending'),
            ('fetching', 'Fetching'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
        ],
        help_text="Status of transcript fetching"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Source Video"
        verbose_name_plural = "Source Videos"
    
    def __str__(self):
        return f"{self.title} ({self.youtube_video_id})"


class TranscriptChunk(models.Model):
    """
    Store transcript entries in the database instead of shared JSON file.
    One SourceVideo -> Many TranscriptChunks
    """
    
    source_video = models.ForeignKey(
        SourceVideo,
        on_delete=models.CASCADE,
        related_name='transcript_chunks',
        help_text="The source video this chunk belongs to"
    )
    
    sequence = models.IntegerField(
        help_text="Order of this chunk in the transcript"
    )
    start = models.FloatField(
        help_text="Start time in seconds"
    )
    duration = models.FloatField(
        help_text="Duration of this chunk in seconds"
    )
    text = models.TextField(
        help_text="The transcript text for this chunk"
    )
    
    processed = models.BooleanField(
        default=False,
        help_text="Whether this chunk has been processed by AI analysis"
    )
    segment_type = models.CharField(
        max_length=50,
        blank=True,
        choices=[
            ('intro', 'Intro'),
            ('reaction', 'Reaction'),
            ('analysis', 'Analysis'),
            ('outro', 'Outro'),
            ('other', 'Other'),
        ],
        help_text="Semantic type of this segment (assigned during analysis)"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['source_video', 'sequence']
        verbose_name = "Transcript Chunk"
        verbose_name_plural = "Transcript Chunks"
        unique_together = [['source_video', 'sequence']]
    
    def __str__(self):
        return f"Chunk {self.sequence}: {self.text[:50]}..."

