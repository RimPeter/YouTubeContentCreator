import re

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from production.models import ArtifactDependency, MediaAsset
from scraper.models import VideoProject

from .access import ProductionValidationError, ensure_production_allowed


def _project_id(value):
    if hasattr(value, "project_id"):
        return value.project_id
    if hasattr(value, "source_video"):
        return value.source_video.project_id
    if hasattr(value, "analysis_run"):
        return value.analysis_run.source_video.project_id
    if hasattr(value, "analysis_segment"):
        return value.analysis_segment.analysis_run.source_video.project_id
    raise ProductionValidationError("The artifact cannot be scoped to a project.")


class ArtifactDependencyService:
    @staticmethod
    def link(
        project,
        upstream,
        downstream,
        user,
        *,
        upstream_version,
        upstream_fingerprint,
        downstream_version,
        relation_type,
    ):
        ensure_production_allowed(project, user)
        if not upstream.pk or not downstream.pk:
            raise ProductionValidationError("Dependency objects must be saved.")
        if _project_id(upstream) != project.pk or _project_id(downstream) != project.pk:
            raise ProductionValidationError("Dependency objects must belong to the project.")
        if not re.fullmatch(r"[0-9a-f]{64}", str(upstream_fingerprint)):
            raise ProductionValidationError("Upstream fingerprint must be a SHA-256 digest.")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(relation_type)):
            raise ProductionValidationError("Relation type must be a lowercase identifier.")
        if int(upstream_version) < 1 or int(downstream_version) < 1:
            raise ProductionValidationError("Dependency versions must be positive.")
        with transaction.atomic():
            locked_project = VideoProject.objects.select_for_update().get(pk=project.pk)
            ensure_production_allowed(locked_project, user)
            dependency, created = ArtifactDependency.objects.get_or_create(
                project=locked_project,
                upstream_content_type=ContentType.objects.get_for_model(upstream),
                upstream_object_id=upstream.pk,
                upstream_version=int(upstream_version),
                downstream_content_type=ContentType.objects.get_for_model(downstream),
                downstream_object_id=downstream.pk,
                downstream_version=int(downstream_version),
                relation_type=relation_type,
                defaults={"upstream_fingerprint": upstream_fingerprint},
            )
            if not created and dependency.upstream_fingerprint != upstream_fingerprint:
                raise ProductionValidationError(
                    "An existing dependency version has a different fingerprint."
                )
            return dependency, created

    @staticmethod
    def mark_media_dependents_stale(project, upstream, current_fingerprint, user):
        ensure_production_allowed(project, user)
        if _project_id(upstream) != project.pk:
            raise ProductionValidationError("The upstream object belongs to another project.")
        if not re.fullmatch(r"[0-9a-f]{64}", str(current_fingerprint)):
            raise ProductionValidationError("Current fingerprint must be a SHA-256 digest.")
        with transaction.atomic():
            locked_project = VideoProject.objects.select_for_update().get(pk=project.pk)
            ensure_production_allowed(locked_project, user)
            media_type = ContentType.objects.get_for_model(MediaAsset)
            stale_ids = list(
                ArtifactDependency.objects.filter(
                    project=locked_project,
                    upstream_content_type=ContentType.objects.get_for_model(upstream),
                    upstream_object_id=upstream.pk,
                    downstream_content_type=media_type,
                )
                .exclude(upstream_fingerprint=current_fingerprint)
                .values_list("downstream_object_id", flat=True)
                .distinct()
            )
            updated = MediaAsset.objects.filter(
                project=locked_project,
                pk__in=stale_ids,
                status__in=[MediaAsset.Status.VALIDATED, MediaAsset.Status.APPROVED],
            ).update(status=MediaAsset.Status.STALE, updated_at=timezone.now())
            return updated
