import re
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


sha256_validator = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="Enter a lowercase SHA-256 digest.",
)


def project_media_upload_to(instance, filename):
    """Return an isolated storage key that never includes a user-supplied basename."""

    suffix = Path(filename).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".bin"
    return f"projects/{instance.project_id}/{instance.kind}/{uuid.uuid4().hex}{suffix}"


class MediaAsset(models.Model):
    class Kind(models.TextChoices):
        SOURCE_UPLOAD = "source_upload", "Source upload"
        SOURCE_CLIP = "source_clip", "Source clip"
        NARRATION = "narration", "Narration"
        VISUAL = "visual", "Visual"
        RENDER = "render", "Render"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        APPROVED = "approved", "Approved"
        STALE = "stale", "Stale"
        SUPERSEDED = "superseded", "Superseded"
        FAILED = "failed", "Failed"

    class Origin(models.TextChoices):
        USER_UPLOAD = "user_upload", "User upload"
        GENERATED = "generated", "Generated"
        IMPORTED = "imported", "Imported"
        PROVIDER = "provider", "External provider"

    class RightsBasis(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        USER_OWNED = "user_owned", "User owned"
        LICENSED = "licensed", "Licensed"
        PERMISSION = "permission", "Permission granted"
        PUBLIC_DOMAIN = "public_domain", "Public domain"

    project = models.ForeignKey(
        "scraper.VideoProject", on_delete=models.CASCADE, related_name="media_assets"
    )
    lineage_id = models.UUIDField(default=uuid.uuid4, editable=False)
    version = models.PositiveIntegerField(default=1)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    display_name = models.CharField(max_length=255)
    file = models.FileField(upload_to=project_media_upload_to, max_length=500, blank=True)
    checksum_sha256 = models.CharField(
        max_length=64, blank=True, validators=[sha256_validator]
    )
    byte_size = models.PositiveBigIntegerField(null=True, blank=True)
    duration_seconds = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    frame_rate = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )
    has_audio = models.BooleanField(default=False)
    has_video = models.BooleanField(default=False)
    detected_mime_type = models.CharField(max_length=100, blank=True)
    container = models.CharField(max_length=100, blank=True)
    video_codec = models.CharField(max_length=100, blank=True)
    audio_codec = models.CharField(max_length=100, blank=True)
    origin = models.CharField(
        max_length=20, choices=Origin.choices, default=Origin.USER_UPLOAD
    )
    source_url = models.URLField(blank=True)
    rights_basis = models.CharField(
        max_length=20, choices=RightsBasis.choices, default=RightsBasis.UNKNOWN
    )
    rights_notes = models.TextField(blank=True)
    consent_metadata = models.JSONField(default=dict, blank=True)
    configuration_snapshot = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="media_assets_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["project", "kind", "lineage_id", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["lineage_id", "version"], name="media_lineage_version_uniq"
            ),
            models.UniqueConstraint(
                fields=["lineage_id"],
                condition=models.Q(status="approved"),
                name="media_one_approved_lineage",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="media_version_gte_1"
            ),
            models.CheckConstraint(
                condition=models.Q(duration_seconds__isnull=True)
                | models.Q(duration_seconds__gte=0),
                name="media_duration_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(frame_rate__isnull=True) | models.Q(frame_rate__gte=0),
                name="media_frame_rate_gte_0",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "status", "kind"], name="media_project_state_idx"
            ),
            models.Index(fields=["checksum_sha256"], name="media_checksum_idx"),
        ]

    def __str__(self):
        return f"{self.display_name} v{self.version}"

    def clean(self):
        errors = {}
        metadata_required = self.status in {
            self.Status.VALIDATED,
            self.Status.APPROVED,
            self.Status.STALE,
            self.Status.SUPERSEDED,
        }
        if metadata_required and not self.file:
            errors["file"] = "This asset status requires a stored file."
        if metadata_required and not self.checksum_sha256:
            errors["checksum_sha256"] = "This asset status requires a checksum."
        if metadata_required and self.byte_size is None:
            errors["byte_size"] = "This asset status requires a byte size."
        if metadata_required and self.validated_at is None:
            errors["validated_at"] = "Current and historical assets require a validation timestamp."
        if self.status == self.Status.APPROVED and self.approved_at is None:
            errors["approved_at"] = "Approved assets require an approval timestamp."
        if self.approved_at is not None and self.status not in {
            self.Status.APPROVED,
            self.Status.STALE,
            self.Status.SUPERSEDED,
        }:
            errors["approved_at"] = "Only approved or historical assets may retain approval."
        if self.status == self.Status.FAILED and not self.error_code:
            errors["error_code"] = "Failed assets require a sanitized error code."
        if errors:
            raise ValidationError(errors)


class PipelineJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    project = models.ForeignKey(
        "scraper.VideoProject", on_delete=models.CASCADE, related_name="pipeline_jobs"
    )
    job_type = models.CharField(max_length=64)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    progress = models.PositiveSmallIntegerField(default=0)
    idempotency_key = models.CharField(max_length=64, validators=[sha256_validator])
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="pipeline_jobs_requested",
    )
    max_attempts = models.PositiveSmallIntegerField(default=3)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    input_fingerprint = models.CharField(max_length=64, validators=[sha256_validator])
    input_snapshot = models.JSONField(default=dict, blank=True)
    configuration_snapshot = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["project", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "job_type", "idempotency_key"],
                condition=models.Q(status__in=["queued", "running"]),
                name="pipeline_active_idem_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(progress__gte=0) & models.Q(progress__lte=100),
                name="pipeline_progress_range",
            ),
            models.CheckConstraint(
                condition=models.Q(max_attempts__gte=1),
                name="pipeline_max_attempts_gte_1",
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_count__lte=models.F("max_attempts")),
                name="pipeline_attempts_within_max",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "status", "-created_at"],
                name="pipeline_project_state_idx",
            ),
            models.Index(
                fields=["job_type", "idempotency_key"],
                name="pipeline_idem_lookup_idx",
            ),
        ]

    def __str__(self):
        return f"{self.job_type} job {self.pk or 'unsaved'} ({self.status})"

    def clean(self):
        errors = {}
        if self.progress > 100:
            errors["progress"] = "Progress must be between 0 and 100."
        if self.max_attempts < 1:
            errors["max_attempts"] = "At least one attempt is required."
        if self.attempt_count > self.max_attempts:
            errors["attempt_count"] = "Attempt count cannot exceed the maximum."
        if self.status == self.Status.RUNNING and self.started_at is None:
            errors["started_at"] = "Running jobs require a start timestamp."
        if self.status == self.Status.SUCCEEDED:
            if self.completed_at is None:
                errors["completed_at"] = "Successful jobs require a completion timestamp."
            if self.progress != 100:
                errors["progress"] = "Successful jobs must have 100 percent progress."
        if self.status == self.Status.FAILED:
            if self.completed_at is None:
                errors["completed_at"] = "Failed jobs require a completion timestamp."
            if not self.error_code:
                errors["error_code"] = "Failed jobs require a sanitized error code."
        if self.status == self.Status.CANCELLED and self.cancelled_at is None:
            errors["cancelled_at"] = "Cancelled jobs require a cancellation timestamp."
        if errors:
            raise ValidationError(errors)


class ArtifactDependency(models.Model):
    project = models.ForeignKey(
        "scraper.VideoProject",
        on_delete=models.CASCADE,
        related_name="artifact_dependencies",
    )
    upstream_content_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, related_name="+"
    )
    upstream_object_id = models.PositiveBigIntegerField()
    upstream_object = GenericForeignKey("upstream_content_type", "upstream_object_id")
    upstream_version = models.PositiveIntegerField()
    upstream_fingerprint = models.CharField(max_length=64, validators=[sha256_validator])
    downstream_content_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, related_name="+"
    )
    downstream_object_id = models.PositiveBigIntegerField()
    downstream_object = GenericForeignKey("downstream_content_type", "downstream_object_id")
    downstream_version = models.PositiveIntegerField()
    relation_type = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["project", "upstream_content_type", "upstream_object_id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "project",
                    "upstream_content_type",
                    "upstream_object_id",
                    "upstream_version",
                    "downstream_content_type",
                    "downstream_object_id",
                    "downstream_version",
                    "relation_type",
                ],
                name="artifact_dependency_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(upstream_version__gte=1),
                name="artifact_upstream_ver_gte_1",
            ),
            models.CheckConstraint(
                condition=models.Q(downstream_version__gte=1),
                name="artifact_downstream_ver_gte_1",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "project",
                    "upstream_content_type",
                    "upstream_object_id",
                    "upstream_version",
                ],
                name="artifact_upstream_idx",
            ),
            models.Index(
                fields=[
                    "project",
                    "downstream_content_type",
                    "downstream_object_id",
                    "downstream_version",
                ],
                name="artifact_downstream_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.upstream_content_type}:{self.upstream_object_id} -> "
            f"{self.downstream_content_type}:{self.downstream_object_id}"
        )
