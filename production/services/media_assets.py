import hashlib
import os
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from production.models import MediaAsset
from scraper.models import VideoProject
from scraper.services import lock_project

from .access import ProductionValidationError, ensure_production_allowed
from .probe import FFprobeClient, MediaProbeError


class MediaAssetValidationError(ProductionValidationError):
    def __init__(self, message, code="invalid_media_upload"):
        self.code = code
        super().__init__(message)


def _display_name(value):
    name = re.split(r"[\\/]", str(value or "upload"))[-1].replace("\x00", "").strip()
    return (name or "upload")[:255]


class MediaAssetService:
    def __init__(self, probe_client=None):
        self.probe_client = probe_client or FFprobeClient()

    @staticmethod
    def _extension(uploaded_file):
        extension = Path(uploaded_file.name or "").suffix.lower()
        allowed = {value.lower() for value in settings.PRODUCTION_ALLOWED_MEDIA_EXTENSIONS}
        if extension not in allowed:
            raise MediaAssetValidationError(
                "This media file extension is not allowed.", "extension_not_allowed"
            )
        return extension

    @staticmethod
    def _spool_and_hash(uploaded_file, extension):
        maximum = settings.PRODUCTION_MAX_UPLOAD_BYTES
        total = 0
        digest = hashlib.sha256()
        temporary = tempfile.NamedTemporaryFile(suffix=extension, delete=False)
        path = temporary.name
        try:
            for chunk in uploaded_file.chunks():
                total += len(chunk)
                if total > maximum:
                    raise MediaAssetValidationError(
                        "The uploaded media file is too large.", "upload_too_large"
                    )
                digest.update(chunk)
                temporary.write(chunk)
            temporary.close()
            if total == 0:
                raise MediaAssetValidationError(
                    "The uploaded media file is empty.", "empty_upload"
                )
            return path, total, digest.hexdigest()
        except Exception:
            temporary.close()
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            raise

    def create_upload(
        self,
        project,
        uploaded_file,
        user,
        *,
        kind=MediaAsset.Kind.SOURCE_UPLOAD,
        display_name=None,
        rights_basis=MediaAsset.RightsBasis.UNKNOWN,
        rights_notes="",
        consent_metadata=None,
        configuration=None,
        previous_asset=None,
    ):
        ensure_production_allowed(project, user)
        extension = self._extension(uploaded_file)
        temporary_path, byte_size, checksum = self._spool_and_hash(uploaded_file, extension)
        stored_name = ""
        transaction_committed = False
        asset = None
        try:
            try:
                metadata = self.probe_client.probe(temporary_path, extension)
            except MediaProbeError as exc:
                raise MediaAssetValidationError(str(exc), exc.code) from exc

            with transaction.atomic():
                locked_project = lock_project(project.pk)
                ensure_production_allowed(locked_project, user)
                lineage_id = None
                version = 1
                if previous_asset is not None:
                    previous = MediaAsset.objects.select_for_update().get(pk=previous_asset.pk)
                    if previous.project_id != locked_project.pk:
                        raise MediaAssetValidationError(
                            "The previous asset belongs to another project."
                        )
                    if previous.kind != kind:
                        raise MediaAssetValidationError(
                            "A new asset version must retain its media kind."
                        )
                    lineage_id = previous.lineage_id
                    version = (
                        MediaAsset.objects.filter(lineage_id=lineage_id).aggregate(
                            latest=Max("version")
                        )["latest"]
                        or 0
                    ) + 1
                asset = MediaAsset(
                    project=locked_project,
                    version=version,
                    kind=kind,
                    status=MediaAsset.Status.VALIDATED,
                    display_name=_display_name(display_name or uploaded_file.name),
                    checksum_sha256=checksum,
                    byte_size=byte_size,
                    duration_seconds=metadata.duration_seconds,
                    width=metadata.width,
                    height=metadata.height,
                    frame_rate=metadata.frame_rate,
                    has_audio=metadata.has_audio,
                    has_video=metadata.has_video,
                    detected_mime_type=metadata.detected_mime_type,
                    container=metadata.container,
                    video_codec=metadata.video_codec,
                    audio_codec=metadata.audio_codec,
                    origin=MediaAsset.Origin.USER_UPLOAD,
                    rights_basis=rights_basis,
                    rights_notes=str(rights_notes)[:5000],
                    consent_metadata=consent_metadata or {},
                    configuration_snapshot=configuration or {},
                    created_by=user,
                    validated_at=timezone.now(),
                )
                if lineage_id is not None:
                    asset.lineage_id = lineage_id
                with open(temporary_path, "rb") as handle:
                    asset.file.save(f"upload{extension}", File(handle), save=False)
                stored_name = asset.file.name
                asset.full_clean()
                asset.save()
            transaction_committed = True
            return asset
        except IntegrityError as exc:
            if asset and stored_name:
                asset.file.storage.delete(stored_name)
            raise MediaAssetValidationError("Could not allocate a media asset version.") from exc
        except Exception:
            if asset and stored_name and not transaction_committed:
                asset.file.storage.delete(stored_name)
            raise
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    def create_generated_file(
        self,
        project,
        local_path,
        user,
        *,
        kind,
        display_name,
        rights_basis,
        rights_notes="",
        consent_metadata=None,
        configuration=None,
        previous_asset=None,
        attempt=None,
    ):
        ensure_production_allowed(project, user)
        local_path = Path(local_path)
        extension = local_path.suffix.lower()
        allowed = {value.lower() for value in settings.PRODUCTION_ALLOWED_MEDIA_EXTENSIONS}
        if extension not in allowed or not local_path.is_file():
            raise MediaAssetValidationError(
                "Generated media is missing or has an unsupported extension.",
                "generated_media_missing",
            )
        byte_size = local_path.stat().st_size
        if byte_size <= 0 or byte_size > settings.PRODUCTION_MAX_UPLOAD_BYTES:
            raise MediaAssetValidationError(
                "Generated media has an invalid byte size.", "generated_media_size"
            )
        digest = hashlib.sha256()
        with local_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        try:
            metadata = self.probe_client.probe(local_path, extension)
        except MediaProbeError as exc:
            raise MediaAssetValidationError(str(exc), exc.code) from exc

        asset = None
        stored_name = ""
        committed = False
        try:
            with transaction.atomic():
                locked_project = lock_project(project.pk)
                ensure_production_allowed(locked_project, user)
                if attempt is not None:
                    from .jobs import PipelineJobService
                    PipelineJobService._running(attempt)
                lineage_id = None
                version = 1
                if previous_asset is not None:
                    previous = MediaAsset.objects.select_for_update().get(pk=previous_asset.pk)
                    if previous.project_id != locked_project.pk or previous.kind != kind:
                        raise MediaAssetValidationError(
                            "The previous generated asset is incompatible."
                        )
                    lineage_id = previous.lineage_id
                    version = (
                        MediaAsset.objects.filter(lineage_id=lineage_id).aggregate(
                            latest=Max("version")
                        )["latest"]
                        or 0
                    ) + 1
                asset = MediaAsset(
                    project=locked_project,
                    version=version,
                    kind=kind,
                    status=MediaAsset.Status.VALIDATED,
                    display_name=_display_name(display_name),
                    checksum_sha256=digest.hexdigest(),
                    byte_size=byte_size,
                    duration_seconds=metadata.duration_seconds,
                    width=metadata.width,
                    height=metadata.height,
                    frame_rate=metadata.frame_rate,
                    has_audio=metadata.has_audio,
                    has_video=metadata.has_video,
                    detected_mime_type=metadata.detected_mime_type,
                    container=metadata.container,
                    video_codec=metadata.video_codec,
                    audio_codec=metadata.audio_codec,
                    origin=MediaAsset.Origin.GENERATED,
                    rights_basis=rights_basis,
                    rights_notes=str(rights_notes)[:5000],
                    consent_metadata=consent_metadata or {},
                    configuration_snapshot=configuration or {},
                    created_by=user,
                    validated_at=timezone.now(),
                )
                if lineage_id is not None:
                    asset.lineage_id = lineage_id
                with local_path.open("rb") as handle:
                    asset.file.save(f"generated{extension}", File(handle), save=False)
                stored_name = asset.file.name
                asset.full_clean()
                asset.save()
            committed = True
            return asset
        except IntegrityError as exc:
            if asset and stored_name:
                asset.file.storage.delete(stored_name)
            raise MediaAssetValidationError("Could not allocate a media asset version.") from exc
        except Exception:
            if asset and stored_name and not committed:
                asset.file.storage.delete(stored_name)
            raise

    @staticmethod
    def approve(asset, user):
        with transaction.atomic():
            lock_project(asset.project_id)
            asset = MediaAsset.objects.select_for_update().select_related("project").get(
                pk=asset.pk
            )
            ensure_production_allowed(asset.project, user)
            if asset.status != MediaAsset.Status.VALIDATED:
                raise MediaAssetValidationError("Only validated assets can be approved.")
            MediaAsset.objects.filter(
                lineage_id=asset.lineage_id,
                status=MediaAsset.Status.APPROVED,
            ).exclude(pk=asset.pk).update(
                status=MediaAsset.Status.SUPERSEDED,
                updated_at=timezone.now(),
            )
            asset.status = MediaAsset.Status.APPROVED
            asset.approved_at = timezone.now()
            asset.full_clean()
            asset.save(update_fields=["status", "approved_at", "updated_at"])
            return asset
