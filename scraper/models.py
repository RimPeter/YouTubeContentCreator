from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


youtube_id_validator = RegexValidator(
    regex=r"^[A-Za-z0-9_-]{11}$",
    message="Enter a valid 11-character YouTube video ID.",
)


class VideoProject(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        TRANSCRIPT_READY = "transcript_ready", "Transcript ready"
        APPROVED = "approved", "Approved"
        ARCHIVED = "archived", "Archived"
        FAILED = "failed", "Failed"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="video_projects",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    expected_duration = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["owner", "status"], name="project_owner_status_idx")]

    def __str__(self):
        return self.title

    @property
    def content_is_mutable(self):
        return self.status not in {self.Status.APPROVED, self.Status.ARCHIVED} and not self.is_locked

    def clean(self):
        errors = {}
        if self.status == self.Status.APPROVED and self.approved_at is None:
            errors["approved_at"] = "Approved projects require an approval timestamp."
        if self.status not in {self.Status.APPROVED, self.Status.ARCHIVED} and self.approved_at is not None:
            errors["approved_at"] = "Only approved or subsequently archived projects may have an approval timestamp."
        if self.status == self.Status.ARCHIVED and self.archived_at is None:
            errors["archived_at"] = "Archived projects require an archive timestamp."
        if self.status != self.Status.ARCHIVED and self.archived_at is not None:
            errors["archived_at"] = "Only archived projects may have an archive timestamp."
        if self.is_locked and self.status != self.Status.APPROVED:
            errors["is_locked"] = "Only approved projects may be locked."
        if self.is_locked and self.locked_at is None:
            errors["locked_at"] = "Locked projects require a lock timestamp."
        if not self.is_locked and self.locked_at is not None:
            errors["locked_at"] = "Unlocked projects may not have a lock timestamp."
        if errors:
            raise ValidationError(errors)


class WorkflowAudit(models.Model):
    project = models.ForeignKey(VideoProject, on_delete=models.PROTECT, related_name="workflow_audits")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=64)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class SourceVideo(models.Model):
    class TranscriptStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        FETCHING = "fetching", "Fetching"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(VideoProject, on_delete=models.CASCADE, related_name="source_videos")
    youtube_url = models.URLField()
    youtube_video_id = models.CharField(max_length=11, validators=[youtube_id_validator])
    title = models.CharField(max_length=255)
    channel = models.CharField(max_length=255, blank=True)
    duration = models.PositiveIntegerField(null=True, blank=True)
    thumbnail_url = models.URLField(blank=True)
    transcript_status = models.CharField(
        max_length=20,
        choices=TranscriptStatus.choices,
        default=TranscriptStatus.PENDING,
    )
    legacy_scraped_video_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        unique=True,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "youtube_video_id"],
                name="unique_project_youtube_video",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "-created_at"], name="source_project_saved_idx"),
        ]

    def __str__(self):
        return self.title


class TranscriptChunk(models.Model):
    source_video = models.ForeignKey(
        SourceVideo,
        on_delete=models.CASCADE,
        related_name="transcript_chunks",
    )
    sequence = models.PositiveIntegerField()
    start_seconds = models.FloatField()
    duration_seconds = models.FloatField()
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["source_video", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_video", "sequence"],
                name="unique_transcript_chunk_sequence",
            ),
            models.CheckConstraint(condition=models.Q(sequence__gte=1), name="chunk_sequence_gte_1"),
            models.CheckConstraint(condition=models.Q(start_seconds__gte=0), name="chunk_start_gte_0"),
            models.CheckConstraint(
                condition=models.Q(duration_seconds__gte=0),
                name="chunk_duration_gte_0",
            ),
        ]

    def __str__(self):
        return f"{self.source_video.youtube_video_id} #{self.sequence}"

    def clean(self):
        errors = {}
        if self.start_seconds < 0:
            errors["start_seconds"] = "Start time cannot be negative."
        if self.duration_seconds < 0:
            errors["duration_seconds"] = "Duration cannot be negative."
        if errors:
            raise ValidationError(errors)


class LegacyImportConflict(models.Model):
    class Severity(models.TextChoices):
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    batch_version = models.CharField(max_length=64)
    legacy_scraped_video_id = models.PositiveBigIntegerField()
    legacy_transcript_entry_id = models.PositiveBigIntegerField(
        default=0,
        help_text="Legacy entry primary key, or 0 for a video-level issue.",
    )
    severity = models.CharField(max_length=10, choices=Severity.choices)
    reason_code = models.CharField(max_length=64)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["legacy_scraped_video_id", "reason_code"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "batch_version",
                    "legacy_scraped_video_id",
                    "legacy_transcript_entry_id",
                    "reason_code",
                ],
                name="unique_legacy_import_issue",
            ),
        ]

    def __str__(self):
        return f"Legacy video {self.legacy_scraped_video_id}: {self.reason_code}"


class ScrapedVideo(models.Model):
    """Transitional legacy record retained during the compatibility window."""

    youtube_video_id = models.CharField(max_length=11, unique=True)
    youtube_url = models.URLField()
    video_title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.video_title


class TranscriptEntry(models.Model):
    """Transitional legacy transcript row; runtime writes are forbidden after cutover."""

    scraped_video = models.ForeignKey(
        ScrapedVideo,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    sequence = models.PositiveIntegerField()
    start_seconds = models.FloatField()
    duration_seconds = models.FloatField()
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scraped_video", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["scraped_video", "sequence"],
                name="unique_transcript_entry_sequence",
            )
        ]

    def __str__(self):
        return f"{self.scraped_video.youtube_video_id} #{self.sequence}"
