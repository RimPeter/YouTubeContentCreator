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
