from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from production.models import ArtifactDependency, MediaAsset, SourceClip, NarrationTake
from scraper.models import VideoProject
from scraper.services import lock_project

from .access import ProductionValidationError


class MediaCleanupService:
    REMOVABLE_STATUSES = (MediaAsset.Status.FAILED, MediaAsset.Status.SUPERSEDED)

    @classmethod
    def _cutoff(cls, older_than_days):
        try:
            older_than_days = int(older_than_days)
        except (TypeError, ValueError) as exc:
            raise ProductionValidationError("Retention age must be a whole number of days.") from exc
        if older_than_days < 1:
            raise ProductionValidationError("Retention age must be at least one day.")
        return timezone.now() - timedelta(days=older_than_days)

    @staticmethod
    def _referenced(asset):
        from editorial.models import TimelineNarrationTake
        media_type = ContentType.objects.get_for_model(MediaAsset)
        return (
            NarrationTake.objects.filter(media_asset=asset).exists()
            or TimelineNarrationTake.objects.filter(media_asset=asset).exists()
            or
            ArtifactDependency.objects.filter(
                Q(upstream_content_type=media_type, upstream_object_id=asset.pk)
                | Q(downstream_content_type=media_type, downstream_object_id=asset.pk)
            ).exists()
            or SourceClip.objects.filter(
                Q(source_asset_id=asset.pk) | Q(processed_asset_id=asset.pk)
            ).exists()
        )

    @classmethod
    def candidates(cls, older_than_days):
        cutoff = cls._cutoff(older_than_days)
        candidates = []
        for asset in MediaAsset.objects.filter(
            status__in=cls.REMOVABLE_STATUSES,
            updated_at__lt=cutoff,
        ).order_by("pk"):
            if not cls._referenced(asset):
                candidates.append(asset)
        return candidates

    @classmethod
    def cleanup(cls, older_than_days, execute=False):
        candidates = cls.candidates(older_than_days)
        if not execute:
            return [(asset.pk, asset.file.name) for asset in candidates]
        cutoff = cls._cutoff(older_than_days)
        result = []
        for candidate in candidates:
            with transaction.atomic():
                try:
                    project = lock_project(candidate.project_id)
                except VideoProject.DoesNotExist:
                    continue
                # The initial list is advisory. A worker or review may have
                # changed eligibility while cleanup waited for this lock.
                asset = MediaAsset.objects.filter(
                    pk=candidate.pk,
                    project=project,
                    status__in=cls.REMOVABLE_STATUSES,
                    updated_at__lt=cutoff,
                ).first()
                if asset is None or cls._referenced(asset):
                    continue
                storage = asset.file.storage
                stored_name = asset.file.name
                result.append((asset.pk, stored_name))
                asset.delete()
                if stored_name:
                    # Files must survive if a surrounding transaction rolls
                    # back the row deletion. Capture each callback's own file.
                    transaction.on_commit(
                        lambda storage=storage, name=stored_name: storage.delete(name)
                    )
        return result
