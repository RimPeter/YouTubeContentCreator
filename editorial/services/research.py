import ipaddress
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from production.models import SourceClip
from production.services.clips import SourceClipService

from editorial.models import EvidenceSource, ReactionBlock, ResearchPackage

from .access import EditorialServiceError, editorial_transaction, ensure_editorial_allowed
from .fingerprints import source_clip_fingerprint


def validate_public_url(value):
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise EditorialServiceError("Enter a valid public HTTP(S) source URL.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise EditorialServiceError("Enter a valid public HTTP(S) source URL.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise EditorialServiceError("Local source URLs are not allowed.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value
    if not address.is_global:
        raise EditorialServiceError("Private or local source URLs are not allowed.")
    return value


class ResearchService:
    @staticmethod
    def create_package(clip, user, *, research_question, editorial_focus):
        with editorial_transaction(clip.project_id, user):
            return ResearchService._create_package(clip, user, research_question, editorial_focus)

    @staticmethod
    def _create_package(clip, user, research_question, editorial_focus):
        clip = SourceClip.objects.select_for_update().select_related(
            "project", "processed_asset", "selected_segment"
        ).get(pk=clip.pk)
        ensure_editorial_allowed(clip.project, user)
        if clip.status != SourceClip.Status.APPROVED:
            raise EditorialServiceError("Research requires an approved source clip.")
        if SourceClipService.is_stale(clip):
            raise EditorialServiceError("The source clip inputs changed; regenerate the clip.")
        version = (
            ResearchPackage.objects.filter(source_clip=clip).aggregate(latest=Max("version"))[
                "latest"
            ]
            or 0
        ) + 1
        package = ResearchPackage(
            project=clip.project,
            source_clip=clip,
            version=version,
            input_fingerprint=source_clip_fingerprint(clip),
            research_question=str(research_question).strip(),
            editorial_focus=str(editorial_focus).strip(),
            configuration_snapshot={"research_contract": "manual-verification-v1"},
            created_by=user,
        )
        try:
            package.full_clean()
        except ValidationError as exc:
            raise EditorialServiceError("Provide a research question and editorial focus.") from exc
        package.save()
        return package

    @staticmethod
    def _editable(package, user):
        ensure_editorial_allowed(package.project, user)
        if package.status != ResearchPackage.Status.DRAFT:
            raise EditorialServiceError("Ready or historical research is immutable.")

    @classmethod
    def add_evidence(cls, package, user, **values):
        with editorial_transaction(package.project_id, user):
            return cls._add_evidence(package, user, values)

    @classmethod
    def _add_evidence(cls, package, user, values):
        package = ResearchPackage.objects.select_for_update().select_related("project").get(
            pk=package.pk
        )
        cls._editable(package, user)
        values["source_url"] = validate_public_url(values.get("source_url", ""))
        evidence = EvidenceSource(
            research_package=package,
            created_by=user,
            verification_status=EvidenceSource.VerificationStatus.UNVERIFIED,
            **values,
        )
        try:
            evidence.full_clean()
        except ValidationError as exc:
            raise EditorialServiceError("The evidence source is incomplete or invalid.") from exc
        evidence.save()
        return evidence

    @classmethod
    def set_verification(cls, evidence, user, *, verified):
        project_id = evidence.research_package.project_id
        with editorial_transaction(project_id, user):
            return cls._set_verification(evidence, user, verified)

    @classmethod
    def _set_verification(cls, evidence, user, verified):
        evidence = EvidenceSource.objects.select_for_update().select_related(
            "research_package__project"
        ).get(pk=evidence.pk)
        cls._editable(evidence.research_package, user)
        if verified:
            evidence.verification_status = EvidenceSource.VerificationStatus.VERIFIED
            evidence.verified_by = user
            evidence.verified_at = timezone.now()
        else:
            evidence.verification_status = EvidenceSource.VerificationStatus.REJECTED
            evidence.verified_by = None
            evidence.verified_at = None
        evidence.full_clean()
        evidence.save(
            update_fields=["verification_status", "verified_by", "verified_at", "updated_at"]
        )
        return evidence

    @classmethod
    def delete_evidence(cls, evidence, user):
        project_id = evidence.research_package.project_id
        with editorial_transaction(project_id, user):
            evidence = EvidenceSource.objects.select_for_update().select_related(
                "research_package__project"
            ).get(pk=evidence.pk)
            cls._editable(evidence.research_package, user)
            evidence.delete()

    @staticmethod
    def mark_ready(package, user):
        """Own the commit boundary so stale-state errors cannot undo invalidation.

        Call outside a caller-managed transaction; Django rejects nested durable
        transactions. Business rejection is raised only after the state commits.
        """
        with editorial_transaction(package.project_id, user, durable=True):
            package, error = ResearchService._mark_ready(package, user)
        if error:
            raise error
        return package

    @staticmethod
    def _mark_ready(package, user):
        package = ResearchPackage.objects.select_for_update().select_related(
            "project", "source_clip__processed_asset", "source_clip__selected_segment"
        ).get(pk=package.pk)
        ensure_editorial_allowed(package.project, user)
        if package.status != ResearchPackage.Status.DRAFT:
            raise EditorialServiceError("Only draft research can be marked ready.")
        if (
            package.source_clip.status != SourceClip.Status.APPROVED
            or SourceClipService.is_stale(package.source_clip)
            or package.input_fingerprint != source_clip_fingerprint(package.source_clip)
        ):
            package.status = ResearchPackage.Status.STALE
            package.save(update_fields=["status", "updated_at"])
            return package, EditorialServiceError(
                "The source clip changed; create a new research version.", "research_stale"
            )
        if package.evidence_sources.filter(
            verification_status=EvidenceSource.VerificationStatus.UNVERIFIED
        ).exists():
            raise EditorialServiceError("Verify or reject every evidence source first.")
        now = timezone.now()
        old_ids = list(
            ResearchPackage.objects.filter(
                source_clip=package.source_clip, status=ResearchPackage.Status.READY
            ).exclude(pk=package.pk).values_list("pk", flat=True)
        )
        if old_ids:
            ResearchPackage.objects.filter(pk__in=old_ids).update(
                status=ResearchPackage.Status.SUPERSEDED, updated_at=now
            )
            ReactionBlock.objects.filter(
                research_package_id__in=old_ids,
                status__in=[ReactionBlock.Status.GENERATING, ReactionBlock.Status.DRAFT, ReactionBlock.Status.APPROVED],
            ).update(status=ReactionBlock.Status.STALE, updated_at=now)
        package.status = ResearchPackage.Status.READY
        package.reviewed_by = user
        package.reviewed_at = now
        package.full_clean()
        package.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        return package, None


def invalidate_clip_editorial_outputs(clip):
    """Invalidate the dependent graph together under the project mutation lock."""
    from scraper.services import lock_project

    with transaction.atomic():
        lock_project(clip.project_id)
        return _invalidate_clip_editorial_outputs(clip)


def _invalidate_clip_editorial_outputs(clip):
    now = timezone.now()
    package_ids = list(clip.research_packages.values_list("pk", flat=True))
    ResearchPackage.objects.filter(
        pk__in=package_ids,
        status__in=[ResearchPackage.Status.DRAFT, ResearchPackage.Status.READY],
    ).update(status=ResearchPackage.Status.STALE, updated_at=now)
    return ReactionBlock.objects.filter(
        source_clip=clip,
        status__in=[ReactionBlock.Status.GENERATING, ReactionBlock.Status.DRAFT, ReactionBlock.Status.APPROVED],
    ).update(status=ReactionBlock.Status.STALE, updated_at=now)
