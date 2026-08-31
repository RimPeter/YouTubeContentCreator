from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from production.models import ArtifactDependency, MediaAsset

from .access import ProductionValidationError


class MediaCleanupService:
    REMOVABLE_STATUSES = (MediaAsset.Status.FAILED, MediaAsset.Status.SUPERSEDED)

    @classmethod
    def candidates(cls, older_than_days):
        try:
            older_than_days = int(older_than_days)
        except (TypeError, ValueError) as exc:
            raise ProductionValidationError("Retention age must be a whole number of days.") from exc
        if older_than_days < 1:
            raise ProductionValidationError("Retention age must be at least one day.")
        cutoff = timezone.now() - timedelta(days=older_than_days)
        media_type = ContentType.objects.get_for_model(MediaAsset)
        candidates = []
        for asset in MediaAsset.objects.filter(
            status__in=cls.REMOVABLE_STATUSES,
            updated_at__lt=cutoff,
        ).order_by("pk"):
            referenced = ArtifactDependency.objects.filter(
                upstream_content_type=media_type,
                upstream_object_id=asset.pk,
            ).exists() or ArtifactDependency.objects.filter(
                downstream_content_type=media_type,
                downstream_object_id=asset.pk,
            ).exists()
            if not referenced:
                candidates.append(asset)
        return candidates

    @classmethod
    def cleanup(cls, older_than_days, execute=False):
        candidates = cls.candidates(older_than_days)
        result = [(asset.pk, asset.file.name) for asset in candidates]
        if not execute:
            return result
        for asset in candidates:
            storage = asset.file.storage
            stored_name = asset.file.name
            with transaction.atomic():
                asset.delete()
            if stored_name:
                storage.delete(stored_name)
        return result
