from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, RegexValidator
from django.db import models


sha256_validator = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="Enter a lowercase SHA-256 digest.",
)


def compose_reaction_script(
    reframe,
    focus,
    evaluation,
    mini_essay_script,
    conclusion,
    bridge="",
):
    return "\n\n".join(
        value.strip()
        for value in (
            reframe,
            focus,
            evaluation,
            mini_essay_script,
            conclusion,
            bridge,
        )
        if value and value.strip()
    )


class ResearchPackage(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        STALE = "stale", "Stale"
        SUPERSEDED = "superseded", "Superseded"

    project = models.ForeignKey(
        "scraper.VideoProject",
        on_delete=models.CASCADE,
        related_name="research_packages",
    )
    source_clip = models.ForeignKey(
        "production.SourceClip",
        on_delete=models.PROTECT,
        related_name="research_packages",
    )
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    input_fingerprint = models.CharField(max_length=64, validators=[sha256_validator])
    research_question = models.CharField(max_length=500)
    editorial_focus = models.TextField(validators=[MaxLengthValidator(4000)])
    configuration_snapshot = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="research_packages_created",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_packages_reviewed",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["source_clip", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_clip", "version"],
                name="research_clip_version_uniq",
            ),
            models.UniqueConstraint(
                fields=["source_clip"],
                condition=models.Q(status="ready"),
                name="research_one_ready_clip",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="research_version_gte_1",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "status", "-created_at"],
                name="research_project_state_idx",
            ),
        ]

    def __str__(self):
        return f"{self.source_clip} research v{self.version}"

    def clean(self):
        errors = {}
        if self.source_clip_id and self.project_id:
            if self.source_clip.project_id != self.project_id:
                errors["source_clip"] = "The source clip must belong to this project."
            if self.status == self.Status.READY and self.source_clip.status != "approved":
                errors["source_clip"] = "Ready research requires an approved source clip."
        if self.status == self.Status.READY and (
            self.reviewed_by_id is None or self.reviewed_at is None
        ):
            errors["reviewed_at"] = "Ready research requires a reviewer and timestamp."
        if errors:
            raise ValidationError(errors)


class ReactionSequencePlan(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready for drafting"
        STALE = "stale", "Stale"
        SUPERSEDED = "superseded", "Superseded"

    project = models.ForeignKey(
        "scraper.VideoProject", on_delete=models.CASCADE, related_name="reaction_sequence_plans"
    )
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    input_fingerprint = models.CharField(max_length=64, validators=[sha256_validator])
    overall_thesis = models.TextField(validators=[MaxLengthValidator(2000)])
    audience_angle = models.TextField(validators=[MaxLengthValidator(2000)])
    planned_conclusion = models.TextField(validators=[MaxLengthValidator(2000)])
    review_attestation = models.JSONField(default=dict, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="reaction_sequence_plans_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="reaction_sequence_plans_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["project", "version"], name="reaction_plan_project_version_uniq"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="reaction_plan_version_gte_1"),
        ]

    def __str__(self):
        return f"{self.project} reaction plan v{self.version}"


class ReactionSequenceDraft(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FAILED = "failed", "Failed"
        STALE = "stale", "Stale"
        SUPERSEDED = "superseded", "Superseded"

    project = models.ForeignKey("scraper.VideoProject", on_delete=models.CASCADE, related_name="reaction_sequence_drafts")
    plan = models.ForeignKey(ReactionSequencePlan, on_delete=models.PROTECT, related_name="reaction_drafts")
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    input_fingerprint = models.CharField(max_length=64, validators=[sha256_validator])
    opening = models.TextField(validators=[MaxLengthValidator(4000)])
    conclusion = models.TextField(validators=[MaxLengthValidator(4000)])
    combined_script = models.TextField(validators=[MaxLengthValidator(40000)])
    rationale = models.TextField(validators=[MaxLengthValidator(4000)])
    provider = models.CharField(max_length=100, blank=True)
    provider_model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=64, blank=True)
    used_fallback = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name="reaction_sequence_drafts_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "version"], name="reaction_sequence_draft_version_uniq"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="reaction_sequence_draft_version_gte_1"),
        ]


class ReactionSequenceDraftSection(models.Model):
    draft = models.ForeignKey(ReactionSequenceDraft, on_delete=models.CASCADE, related_name="sections")
    plan_section = models.ForeignKey("ReactionSequenceSection", on_delete=models.PROTECT, related_name="draft_sections")
    order = models.PositiveIntegerField()
    reaction_text = models.TextField(validators=[MaxLengthValidator(8000)])
    bridge = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])

    class Meta:
        ordering = ["draft", "order"]
        constraints = [
            models.UniqueConstraint(fields=["draft", "order"], name="reaction_sequence_draft_section_order_uniq"),
            models.UniqueConstraint(fields=["draft", "plan_section"], name="reaction_sequence_draft_plan_section_uniq"),
            models.CheckConstraint(condition=models.Q(order__gte=1), name="reaction_sequence_draft_section_order_gte_1"),
        ]


class ReactionTimeline(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RECORDING = "recording", "Ready for recording"
        READY = "ready", "Recording complete"
        STALE = "stale", "Stale"

    project = models.ForeignKey("scraper.VideoProject", on_delete=models.CASCADE, related_name="reaction_timelines")
    reaction_draft = models.ForeignKey(ReactionSequenceDraft, on_delete=models.PROTECT, related_name="timelines")
    reviewed_fingerprint = models.CharField(max_length=64, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="recording_reviews")
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    input_fingerprint = models.CharField(max_length=64, validators=[sha256_validator])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name="reaction_timelines_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["reaction_draft", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["reaction_draft", "version"], name="reaction_timeline_draft_version_uniq"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="reaction_timeline_version_gte_1"),
        ]


class ReactionTimelineItem(models.Model):
    revision = models.PositiveIntegerField(default=1)
    class ItemType(models.TextChoices):
        SOURCE = "source", "Source"
        CREATOR = "creator", "Creator"

    timeline = models.ForeignKey(ReactionTimeline, on_delete=models.CASCADE, related_name="items")
    order = models.PositiveIntegerField()
    item_type = models.CharField(max_length=12, choices=ItemType.choices)
    plan_section = models.ForeignKey("ReactionSequenceSection", on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="timeline_items")
    draft_section = models.ForeignKey(ReactionSequenceDraftSection, on_delete=models.PROTECT, null=True, blank=True,
                                      related_name="timeline_items")
    label = models.CharField(max_length=255)
    transcript_text = models.TextField()
    included = models.BooleanField(default=True)
    source_start_seconds = models.FloatField(null=True, blank=True)
    source_end_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["timeline", "order"]
        constraints = [
            models.UniqueConstraint(fields=["timeline", "order"], name="reaction_timeline_item_order_uniq"),
            models.CheckConstraint(condition=models.Q(order__gte=1), name="reaction_timeline_item_order_gte_1"),
            models.CheckConstraint(condition=models.Q(source_end_seconds__isnull=True) | models.Q(source_end_seconds__gte=models.F("source_start_seconds")), name="reaction_timeline_time_order"),
        ]


class TimelineNarrationTake(models.Model):
    recording_notes = models.TextField(blank=True)
    selected = models.BooleanField(default=False)
    timeline_item = models.ForeignKey(ReactionTimelineItem, on_delete=models.PROTECT, related_name="narration_takes")
    media_asset = models.OneToOneField("production.MediaAsset", on_delete=models.PROTECT, related_name="timeline_narration_take")
    version = models.PositiveIntegerField()
    script_text = models.TextField()
    script_fingerprint = models.CharField(max_length=64, validators=[sha256_validator])
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="timeline_narration_takes_approved")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name="timeline_narration_takes_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timeline_item", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["timeline_item", "version"], name="timeline_narration_item_version_uniq"),
            models.UniqueConstraint(fields=["timeline_item"], condition=models.Q(selected=True), name="one_selected_timeline_take"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="timeline_narration_version_gte_1"),
        ]


class ReactionProductionAssembly(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready for production"
        STALE = "stale", "Stale"

    timeline = models.ForeignKey(ReactionTimeline, on_delete=models.PROTECT, related_name="production_assemblies")
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    input_fingerprint = models.CharField(max_length=64, validators=[sha256_validator])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name="reaction_production_assemblies_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timeline", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["timeline", "version"], name="reaction_assembly_timeline_version_uniq"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="reaction_assembly_version_gte_1"),
        ]


class ReactionProductionAssemblyItem(models.Model):
    snapshot = models.JSONField(default=dict)
    assembly = models.ForeignKey(ReactionProductionAssembly, on_delete=models.CASCADE, related_name="items")
    order = models.PositiveIntegerField()
    timeline_item = models.ForeignKey(ReactionTimelineItem, on_delete=models.PROTECT, related_name="assembly_items")
    narration_take = models.ForeignKey(TimelineNarrationTake, on_delete=models.PROTECT, null=True, blank=True,
                                      related_name="assembly_items")
    source_clip = models.ForeignKey("production.SourceClip", on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="reaction_assembly_items")
    transcript_text = models.TextField()
    source_start_seconds = models.FloatField(null=True, blank=True)
    source_end_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["assembly", "order"]
        constraints = [
            models.UniqueConstraint(fields=["assembly", "order"], name="reaction_assembly_item_order_uniq"),
            models.CheckConstraint(condition=models.Q(order__gte=1), name="reaction_assembly_item_order_gte_1"),
        ]


class ReactionSequenceSection(models.Model):
    class Role(models.TextChoices):
        THESIS = "thesis", "Thesis"
        EVIDENCE = "evidence", "Evidence"
        TENSION = "tension", "Tension"
        REFLECTION = "reflection", "Reflection"
        ADD_ON = "add_on", "Practical add-on"
        CONCLUSION = "conclusion", "Conclusion"

    plan = models.ForeignKey(ReactionSequencePlan, on_delete=models.CASCADE, related_name="sections")
    selection = models.ForeignKey(
        "analysis.SegmentSelection", on_delete=models.PROTECT, related_name="reaction_sequence_sections"
    )
    order = models.PositiveIntegerField()
    role = models.CharField(max_length=16, choices=Role.choices)
    source_title = models.CharField(max_length=255)
    transcript_snapshot = models.TextField()
    recommendation_snapshot = models.JSONField(default=dict)
    bridge = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])
    research_required = models.BooleanField(default=False)
    research_reason = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])

    class Meta:
        ordering = ["plan", "order"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "order"], name="reaction_plan_section_order_uniq"),
            models.UniqueConstraint(fields=["plan", "selection"], name="reaction_plan_section_selection_uniq"),
            models.CheckConstraint(condition=models.Q(order__gte=1), name="reaction_plan_section_order_gte_1"),
        ]

    def __str__(self):
        return f"{self.plan} section {self.order}: {self.source_title}"


class EvidenceSource(models.Model):
    class Classification(models.TextChoices):
        SUPPORTS = "supports", "Supports"
        CONTRADICTS = "contradicts", "Contradicts"
        UNCERTAIN = "uncertain", "Uncertain"
        CONTEXT = "context", "Context"

    class VerificationStatus(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    class SourceOrigin(models.TextChoices):
        MANUAL = "manual", "Entered by user"
        PROVIDER = "provider", "Suggested by provider"

    research_package = models.ForeignKey(
        ResearchPackage,
        on_delete=models.CASCADE,
        related_name="evidence_sources",
    )
    source_url = models.URLField(max_length=1000)
    title = models.CharField(max_length=500)
    publisher = models.CharField(max_length=255, blank=True)
    author = models.CharField(max_length=255, blank=True)
    publication_date = models.DateField(null=True, blank=True)
    retrieved_on = models.DateField()
    classification = models.CharField(max_length=16, choices=Classification.choices)
    finding = models.TextField(validators=[MaxLengthValidator(4000)])
    relevance = models.TextField(validators=[MaxLengthValidator(4000)])
    permitted_excerpt = models.TextField(
        blank=True,
        validators=[MaxLengthValidator(1000)],
        help_text="Store only a short excerpt when permitted.",
    )
    citation_metadata = models.JSONField(default=dict, blank=True)
    snapshot_reference = models.URLField(max_length=1000, blank=True)
    source_origin = models.CharField(
        max_length=16,
        choices=SourceOrigin.choices,
        default=SourceOrigin.MANUAL,
    )
    verification_status = models.CharField(
        max_length=16,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="evidence_sources_created",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evidence_sources_verified",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["research_package", "title", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["research_package", "source_url"],
                name="evidence_package_url_uniq",
            ),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        errors = {}
        if self.verification_status == self.VerificationStatus.VERIFIED and (
            self.verified_by_id is None or self.verified_at is None
        ):
            errors["verified_at"] = "Verified evidence requires a verifier and timestamp."
        if self.verification_status != self.VerificationStatus.VERIFIED and (
            self.verified_by_id is not None or self.verified_at is not None
        ):
            errors["verified_at"] = "Only verified evidence may retain verification metadata."
        if errors:
            raise ValidationError(errors)


class ReactionBlock(models.Model):
    class Status(models.TextChoices):
        GENERATING = "generating", "Generating"
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        FAILED = "failed", "Failed"
        STALE = "stale", "Stale"
        SUPERSEDED = "superseded", "Superseded"

    class ReactionType(models.TextChoices):
        AGREEMENT = "agreement", "Agreement"
        DISAGREEMENT = "disagreement", "Disagreement"
        CRITIQUE = "critique", "Critique"
        CONTEXT = "context", "Additional context"
        CORRECTION = "correction", "Correction"
        MIXED = "mixed", "Mixed"

    project = models.ForeignKey(
        "scraper.VideoProject",
        on_delete=models.CASCADE,
        related_name="reaction_blocks",
    )
    source_clip = models.ForeignKey(
        "production.SourceClip",
        on_delete=models.PROTECT,
        related_name="reaction_blocks",
    )
    research_package = models.ForeignKey(
        ResearchPackage,
        on_delete=models.PROTECT,
        related_name="reaction_blocks",
    )
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.GENERATING,
    )
    input_fingerprint = models.CharField(max_length=64, validators=[sha256_validator])
    reframe = models.TextField(validators=[MaxLengthValidator(4000)])
    focus = models.TextField(validators=[MaxLengthValidator(4000)])
    reaction_type = models.CharField(max_length=16, choices=ReactionType.choices)
    evaluation = models.TextField(validators=[MaxLengthValidator(8000)])
    mini_essay_thesis = models.TextField(validators=[MaxLengthValidator(2000)])
    mini_essay_script = models.TextField(validators=[MaxLengthValidator(16000)])
    conclusion = models.TextField(validators=[MaxLengthValidator(4000)])
    bridge = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])
    combined_script = models.TextField(validators=[MaxLengthValidator(40000)])
    rationale = models.TextField(validators=[MaxLengthValidator(4000)])
    provider = models.CharField(max_length=100, blank=True)
    provider_model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=64, blank=True)
    algorithm_version = models.CharField(max_length=64)
    configuration_snapshot = models.JSONField(default=dict, blank=True)
    used_fallback = models.BooleanField(default=False)
    approval_attestation = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="reaction_blocks_created",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reaction_blocks_approved",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["source_clip", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_clip", "version"],
                name="reaction_clip_version_uniq",
            ),
            models.UniqueConstraint(
                fields=["source_clip"],
                condition=models.Q(status="approved"),
                name="reaction_one_approved_clip",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="reaction_version_gte_1",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "status", "-created_at"],
                name="reaction_project_state_idx",
            ),
        ]

    def __str__(self):
        return f"{self.source_clip} reaction v{self.version}"

    def clean(self):
        errors = {}
        if self.source_clip_id and self.project_id and self.source_clip.project_id != self.project_id:
            errors["source_clip"] = "The source clip must belong to this project."
        if self.research_package_id and self.source_clip_id:
            if self.research_package.source_clip_id != self.source_clip_id:
                errors["research_package"] = "Research must belong to this source clip."
        expected_script = compose_reaction_script(
            self.reframe,
            self.focus,
            self.evaluation,
            self.mini_essay_script,
            self.conclusion,
            self.bridge,
        )
        if self.combined_script and self.combined_script != expected_script:
            errors["combined_script"] = "Combined script must match its stored subsections."
        if self.status == self.Status.APPROVED:
            if self.approved_by_id is None or self.approved_at is None:
                errors["approved_at"] = "Approved reactions require an approver and timestamp."
            if not self.approval_attestation.get("evidence_reviewed") or not self.approval_attestation.get(
                "originality_confirmed"
            ):
                errors["approval_attestation"] = "Approval attestations are required."
        if self.status == self.Status.FAILED and not self.error_code:
            errors["error_code"] = "Failed reactions require a sanitized error code."
        if errors:
            raise ValidationError(errors)


class ReactionClaim(models.Model):
    class ClaimType(models.TextChoices):
        FACTUAL = "factual", "Factual"
        OPINION = "opinion", "Opinion"
        INFERENCE = "inference", "Inference"

    reaction_block = models.ForeignKey(
        ReactionBlock,
        on_delete=models.CASCADE,
        related_name="claims",
    )
    order = models.PositiveIntegerField()
    text = models.TextField(validators=[MaxLengthValidator(4000)])
    claim_type = models.CharField(max_length=16, choices=ClaimType.choices)
    transcript_chunk = models.ForeignKey(
        "scraper.TranscriptChunk",
        on_delete=models.PROTECT,
        related_name="reaction_claims",
        null=True,
        blank=True,
    )
    evidence_source = models.ForeignKey(
        EvidenceSource,
        on_delete=models.PROTECT,
        related_name="reaction_claims",
        null=True,
        blank=True,
    )
    citation_note = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["reaction_block", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["reaction_block", "order"],
                name="reaction_claim_order_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(order__gte=1),
                name="reaction_claim_order_gte_1",
            ),
            models.CheckConstraint(
                condition=~models.Q(claim_type="factual")
                | models.Q(transcript_chunk__isnull=False)
                | models.Q(evidence_source__isnull=False),
                name="factual_claim_has_citation",
            ),
        ]

    def __str__(self):
        return f"{self.reaction_block} claim {self.order}"

    def clean(self):
        errors = {}
        if self.transcript_chunk_id and self.reaction_block_id:
            segment = self.reaction_block.source_clip.selected_segment.analysis_segment
            if self.transcript_chunk.source_video_id != segment.analysis_run.source_video_id:
                errors["transcript_chunk"] = "Transcript evidence must use the selected source."
            elif not (
                segment.start_chunk.sequence
                <= self.transcript_chunk.sequence
                <= segment.end_chunk.sequence
            ):
                errors["transcript_chunk"] = "Transcript evidence must be within the segment."
        if self.evidence_source_id and self.reaction_block_id:
            if self.evidence_source.research_package_id != self.reaction_block.research_package_id:
                errors["evidence_source"] = "Evidence must belong to the reaction research package."
            elif self.evidence_source.verification_status != EvidenceSource.VerificationStatus.VERIFIED:
                errors["evidence_source"] = "External evidence must be human-verified."
        if self.claim_type == self.ClaimType.FACTUAL and not (
            self.transcript_chunk_id or self.evidence_source_id
        ):
            errors["claim_type"] = "Factual claims require transcript or verified evidence."
        if errors:
            raise ValidationError(errors)
