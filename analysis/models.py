from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AnalysisRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    source_video = models.ForeignKey(
        "scraper.SourceVideo",
        on_delete=models.CASCADE,
        related_name="analysis_runs",
    )
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    source_fingerprint = models.CharField(max_length=64)
    provider = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=64, blank=True)
    algorithm_version = models.CharField(max_length=64)
    configuration = models.JSONField(default=dict)
    used_fallback = models.BooleanField(default=False)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="analysis_runs_created",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["source_video", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_video", "version"],
                name="analysis_run_source_ver_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="analysis_run_version_gte_1",
            ),
        ]
        indexes = [
            models.Index(
                fields=["source_video", "status", "-version"],
                name="analysis_run_lookup_idx",
            ),
        ]

    def __str__(self):
        return f"{self.source_video} analysis v{self.version}"


class AnalysisSegment(models.Model):
    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="segments",
    )
    order = models.PositiveIntegerField()
    start_chunk = models.ForeignKey(
        "scraper.TranscriptChunk",
        on_delete=models.PROTECT,
        related_name="analysis_segments_starting_here",
    )
    end_chunk = models.ForeignKey(
        "scraper.TranscriptChunk",
        on_delete=models.PROTECT,
        related_name="analysis_segments_ending_here",
    )
    start_seconds = models.FloatField()
    end_seconds = models.FloatField()
    title = models.CharField(max_length=255)
    summary = models.TextField()
    source_text = models.TextField()
    topic_labels = models.JSONField(default=list)
    component_scores = models.JSONField(default=dict)
    aggregate_score = models.DecimalField(max_digits=6, decimal_places=3)
    rationale = models.TextField()
    editorial_recommendation = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["analysis_run", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis_run", "order"],
                name="analysis_segment_order_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(order__gte=1),
                name="analysis_segment_order_gte_1",
            ),
            models.CheckConstraint(
                condition=models.Q(end_seconds__gte=models.F("start_seconds")),
                name="analysis_segment_time_order",
            ),
            models.CheckConstraint(
                condition=models.Q(start_seconds__gte=0),
                name="analysis_segment_start_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(aggregate_score__gte=0)
                & models.Q(aggregate_score__lte=100),
                name="analysis_segment_score_range",
            ),
        ]

    def __str__(self):
        return f"Analysis {self.analysis_run_id} segment {self.order}: {self.title}"

    def clean(self):
        errors = {}
        if self.start_chunk_id and self.end_chunk_id:
            if self.start_chunk.source_video_id != self.end_chunk.source_video_id:
                errors["end_chunk"] = "Segment boundaries must belong to the same source video."
            elif self.start_chunk.sequence > self.end_chunk.sequence:
                errors["end_chunk"] = "The end chunk must not precede the start chunk."
            if (
                self.analysis_run_id
                and self.start_chunk.source_video_id != self.analysis_run.source_video_id
            ):
                errors["start_chunk"] = "Segment chunks must belong to the analysis source video."
        if errors:
            raise ValidationError(errors)


class AnalysisSegmentReview(models.Model):
    """An append-only editorial decision about an AI-suggested segment."""

    class Decision(models.TextChoices):
        SELECTED = "selected", "Selected"
        EXCLUDED = "excluded", "Excluded"
        REMOVED = "removed", "Removed from selection"

    project = models.ForeignKey(
        "scraper.VideoProject",
        on_delete=models.CASCADE,
        related_name="analysis_segment_reviews",
    )
    analysis_segment = models.ForeignKey(
        AnalysisSegment,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    decision = models.CharField(max_length=12, choices=Decision.choices)
    reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="analysis_segment_reviews_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["analysis_segment", "-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=["project", "analysis_segment", "-created_at"],
                name="analysis_review_lookup_idx",
            ),
        ]

    def __str__(self):
        return f"{self.analysis_segment} — {self.get_decision_display()}"

    def clean(self):
        if (
            self.analysis_segment_id
            and self.project_id
            and self.analysis_segment.analysis_run.source_video.project_id != self.project_id
        ):
            raise ValidationError(
                {"analysis_segment": "The segment must belong to this project."}
            )


class SegmentSelectionQuerySet(models.QuerySet):
    def active(self):
        return self.filter(retired_at__isnull=True)


class SegmentSelection(models.Model):
    # The default manager deliberately includes history. Worklists opt into active().
    objects = SegmentSelectionQuerySet.as_manager()
    project = models.ForeignKey(
        "scraper.VideoProject",
        on_delete=models.CASCADE,
        related_name="segment_selections",
    )
    analysis_segment = models.ForeignKey(
        AnalysisSegment,
        on_delete=models.PROTECT,
        related_name="selections",
    )
    order = models.PositiveIntegerField()
    reviewed_start_seconds = models.FloatField(null=True, blank=True)
    reviewed_end_seconds = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True)
    selected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="segment_selections_created",
    )
    retired_at = models.DateTimeField(null=True, blank=True, editable=False)
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="segment_selections_retired",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "analysis_segment"],
                condition=models.Q(retired_at__isnull=True),
                name="selection_active_segment_uniq",
            ),
            models.UniqueConstraint(
                fields=["project", "order"],
                condition=models.Q(retired_at__isnull=True),
                name="selection_active_order_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(order__gte=1),
                name="selection_order_gte_1",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reviewed_start_seconds__isnull=True, reviewed_end_seconds__isnull=True)
                    | models.Q(
                        reviewed_start_seconds__isnull=False,
                        reviewed_end_seconds__isnull=False,
                    )
                ),
                name="selection_reviewed_times_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reviewed_start_seconds__isnull=True)
                    | models.Q(reviewed_end_seconds__gt=models.F("reviewed_start_seconds"))
                ),
                name="selection_reviewed_time_order",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reviewed_start_seconds__isnull=True)
                    | models.Q(reviewed_start_seconds__gte=0)
                ),
                name="selection_reviewed_start_gte_0",
            ),
        ]

    def __str__(self):
        return f"{self.project} selection {self.order}"

    def clean(self):
        errors = {}
        if self.analysis_segment_id and self.project_id:
            segment = self.analysis_segment
            if segment.analysis_run.source_video.project_id != self.project_id:
                errors["analysis_segment"] = "The segment must belong to this project."
            if segment.analysis_run.status != AnalysisRun.Status.SUCCEEDED:
                errors["analysis_segment"] = "Only successful analysis segments may be selected."
        if (self.reviewed_start_seconds is None) != (self.reviewed_end_seconds is None):
            errors["reviewed_start_seconds"] = "Provide both reviewed boundaries or neither."
        if self.reviewed_start_seconds is not None and self.analysis_segment_id:
            if self.reviewed_start_seconds < self.analysis_segment.start_seconds:
                errors["reviewed_start_seconds"] = "Reviewed start cannot precede the segment."
            if self.reviewed_end_seconds > self.analysis_segment.end_seconds:
                errors["reviewed_end_seconds"] = "Reviewed end cannot exceed the segment."
        if errors:
            raise ValidationError(errors)
