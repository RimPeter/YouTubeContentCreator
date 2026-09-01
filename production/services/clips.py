import os
import tempfile
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from analysis.models import SegmentSelection
from analysis.services.analysis import AnalysisService
from production.models import MediaAsset, PipelineJob, SourceClip
from scraper.models import VideoProject

from .access import ProductionValidationError, ensure_production_allowed
from .dependencies import ArtifactDependencyService
from .ffmpeg import FFmpegClient, FFmpegError
from .fingerprints import fingerprint_json
from .jobs import PipelineJobService, PipelineJobStateError
from .media_assets import MediaAssetService, MediaAssetValidationError


TRIM_ALGORITHM_VERSION = "source-clip-v1"


class SourceClipError(ProductionValidationError):
    def __init__(self, message, code="source_clip_invalid"):
        self.code = code
        super().__init__(message)


def _decimal(value, field_name):
    try:
        result = Decimal(str(value)).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SourceClipError(f"{field_name} must be numeric.") from exc
    if not result.is_finite():
        raise SourceClipError(f"{field_name} must be finite.")
    return result


@contextmanager
def materialized_asset(asset):
    try:
        local_path = asset.file.path
    except (AttributeError, NotImplementedError):
        suffix = Path(asset.file.name).suffix.lower() or ".bin"
        temporary = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        local_path = temporary.name
        try:
            with asset.file.storage.open(asset.file.name, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    temporary.write(chunk)
            temporary.close()
            yield local_path
        finally:
            temporary.close()
            try:
                os.unlink(local_path)
            except FileNotFoundError:
                pass
    else:
        if not Path(local_path).is_file():
            raise SourceClipError("The source media file is missing.", "source_file_missing")
        yield local_path


class SourceClipService:
    def __init__(self, ffmpeg_client=None, media_service=None):
        self.ffmpeg_client = ffmpeg_client or FFmpegClient()
        self.media_service = media_service or MediaAssetService()

    @staticmethod
    def _selection_boundaries(selection):
        segment = selection.analysis_segment
        start = (
            selection.reviewed_start_seconds
            if selection.reviewed_start_seconds is not None
            else segment.start_seconds
        )
        end = (
            selection.reviewed_end_seconds
            if selection.reviewed_end_seconds is not None
            else segment.end_seconds
        )
        return _decimal(start, "Selection start"), _decimal(end, "Selection end")

    @staticmethod
    def _selection_fingerprint(selection):
        run = selection.analysis_segment.analysis_run
        start, end = SourceClipService._selection_boundaries(selection)
        return fingerprint_json(
            {
                "selection_id": selection.pk,
                "start_seconds": str(start),
                "end_seconds": str(end),
                "analysis_run_id": run.pk,
                "analysis_version": run.version,
                "analysis_source_fingerprint": run.source_fingerprint,
            }
        )

    @classmethod
    def _input_fingerprint(cls, selection, source_asset, configuration):
        return fingerprint_json(
            {
                "algorithm": TRIM_ALGORITHM_VERSION,
                "selection_fingerprint": cls._selection_fingerprint(selection),
                "source_asset_id": source_asset.pk,
                "source_asset_version": source_asset.version,
                "source_checksum": source_asset.checksum_sha256,
                "configuration": configuration,
            }
        )

    @staticmethod
    def _validate_source(selection, source_asset):
        project = selection.project
        if source_asset.project_id != project.pk:
            raise SourceClipError("The source media belongs to another project.")
        if source_asset.kind != MediaAsset.Kind.SOURCE_UPLOAD:
            raise SourceClipError("Choose a validated source-video upload.")
        if source_asset.status not in {
            MediaAsset.Status.VALIDATED,
            MediaAsset.Status.APPROVED,
        }:
            raise SourceClipError("The source upload is not current and validated.")
        if not source_asset.has_video or source_asset.duration_seconds is None:
            raise SourceClipError("The source upload requires a video stream and duration.")
        if source_asset.rights_basis == MediaAsset.RightsBasis.UNKNOWN or not source_asset.consent_metadata.get(
            "rights_confirmed"
        ):
            raise SourceClipError(
                "Confirm the right to use this source before creating clips.",
                "rights_not_confirmed",
            )
        if AnalysisService.is_stale(selection.analysis_segment.analysis_run):
            raise SourceClipError("The selected analysis is stale and must be rerun.")

    @classmethod
    def current_input_fingerprint(cls, clip):
        return cls._input_fingerprint(
            clip.selected_segment,
            clip.source_asset,
            {
                "padding_before_seconds": str(clip.padding_before_seconds),
                "padding_after_seconds": str(clip.padding_after_seconds),
            },
        )

    @classmethod
    def is_stale(cls, clip):
        return clip.input_fingerprint != cls.current_input_fingerprint(clip)

    def create_clip(
        self,
        selection,
        source_asset,
        user,
        *,
        padding_before=0,
        padding_after=0,
    ):
        ensure_production_allowed(selection.project, user)
        self._validate_source(selection, source_asset)
        requested_start, requested_end = self._selection_boundaries(selection)
        if requested_end <= requested_start:
            raise SourceClipError("The selected segment has no positive duration.")
        padding_before = _decimal(padding_before, "Padding before")
        padding_after = _decimal(padding_after, "Padding after")
        maximum_padding = Decimal(str(settings.SOURCE_CLIP_MAX_PADDING_SECONDS))
        if not (Decimal("0") <= padding_before <= maximum_padding) or not (
            Decimal("0") <= padding_after <= maximum_padding
        ):
            raise SourceClipError("Clip padding is outside the configured limit.")
        source_duration = _decimal(source_asset.duration_seconds, "Source duration")
        tolerance = Decimal(str(settings.SOURCE_CLIP_DURATION_TOLERANCE_SECONDS))
        if requested_end > source_duration + tolerance:
            raise SourceClipError("The selected timestamps exceed the uploaded video duration.")
        actual_start = max(Decimal("0"), requested_start - padding_before)
        actual_end = min(source_duration, requested_end + padding_after)
        expected_duration = (actual_end - actual_start).quantize(Decimal("0.001"))
        if expected_duration <= 0:
            raise SourceClipError("The resolved clip has no positive duration.")
        configuration = {
            "padding_before_seconds": str(padding_before),
            "padding_after_seconds": str(padding_after),
        }
        input_fingerprint = self._input_fingerprint(selection, source_asset, configuration)
        job, job_created = PipelineJobService.enqueue(
            selection.project,
            "clip_trim",
            user,
            input_snapshot={
                "selection_id": selection.pk,
                "selection_version": selection.analysis_segment.analysis_run.version,
                "source_asset_id": source_asset.pk,
                "source_asset_version": source_asset.version,
            },
            configuration=configuration,
            input_fingerprint=input_fingerprint,
            max_attempts=2,
        )
        if not job_created:
            existing = SourceClip.objects.filter(pipeline_job=job).first()
            if existing:
                return existing, False
            raise SourceClipError("An equivalent clip job is already active.", "clip_job_active")

        try:
            with transaction.atomic():
                locked_project = VideoProject.objects.select_for_update().get(
                    pk=selection.project_id
                )
                ensure_production_allowed(locked_project, user)
                locked_selection = SegmentSelection.objects.select_for_update().select_related(
                    "analysis_segment__analysis_run__source_video"
                ).get(pk=selection.pk, project=locked_project)
                locked_asset = MediaAsset.objects.select_for_update().get(
                    pk=source_asset.pk, project=locked_project
                )
                self._validate_source(locked_selection, locked_asset)
                if self._input_fingerprint(
                    locked_selection, locked_asset, configuration
                ) != input_fingerprint:
                    raise SourceClipError(
                        "Clip inputs changed while the job was being prepared.",
                        "clip_inputs_changed",
                    )
                version = (
                    SourceClip.objects.filter(selected_segment=locked_selection).aggregate(
                        latest=Max("version")
                    )["latest"]
                    or 0
                ) + 1
                clip = SourceClip(
                    project=locked_project,
                    selected_segment=locked_selection,
                    source_asset=locked_asset,
                    pipeline_job=job,
                    version=version,
                    status=SourceClip.Status.PROCESSING,
                    input_fingerprint=input_fingerprint,
                    requested_start_seconds=requested_start,
                    requested_end_seconds=requested_end,
                    padding_before_seconds=padding_before,
                    padding_after_seconds=padding_after,
                    expected_duration_seconds=expected_duration,
                    created_by=user,
                )
                clip.full_clean()
                clip.save()
        except SourceClipError as exc:
            job = PipelineJobService.start(job)
            PipelineJobService.fail(job, exc.code, str(exc))
            raise
        except (IntegrityError, SegmentSelection.DoesNotExist, MediaAsset.DoesNotExist) as exc:
            job = PipelineJobService.start(job)
            PipelineJobService.fail(job, "clip_persistence_failed", "Clip setup could not be saved.")
            raise SourceClipError("Clip setup could not be saved.", "clip_persistence_failed") from exc

        job = PipelineJobService.start(job)
        output_handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        output_path = output_handle.name
        output_handle.close()
        processed_asset = None
        try:
            previous_clip = (
                SourceClip.objects.filter(
                    selected_segment=clip.selected_segment,
                    processed_asset__isnull=False,
                )
                .exclude(pk=clip.pk)
                .select_related("processed_asset")
                .order_by("-version")
                .first()
            )
            with materialized_asset(source_asset) as input_path:
                self.ffmpeg_client.trim(input_path, output_path, actual_start, actual_end)
            job = PipelineJobService.update_progress(job, 70)
            processed_asset = self.media_service.create_generated_file(
                clip.project,
                output_path,
                user,
                kind=MediaAsset.Kind.SOURCE_CLIP,
                display_name=f"{clip.selected_segment.analysis_segment.title} clip v{clip.version}.mp4",
                rights_basis=source_asset.rights_basis,
                rights_notes=source_asset.rights_notes,
                consent_metadata=source_asset.consent_metadata,
                configuration={
                    "algorithm": TRIM_ALGORITHM_VERSION,
                    **configuration,
                    "source_asset_id": source_asset.pk,
                },
                previous_asset=previous_clip.processed_asset if previous_clip else None,
            )
            self._validate_output(processed_asset, expected_duration)
            validation_result = {
                "playable": True,
                "has_video": processed_asset.has_video,
                "has_audio": processed_asset.has_audio,
                "duration_within_tolerance": True,
                "timestamps_within_source": True,
                "resolution_supported": True,
                "frame_rate_supported": True,
                "expected_duration_seconds": str(expected_duration),
                "actual_duration_seconds": str(processed_asset.duration_seconds),
                "tolerance_seconds": str(tolerance),
            }
            with transaction.atomic():
                locked_project = VideoProject.objects.select_for_update().get(pk=clip.project_id)
                ensure_production_allowed(locked_project, user)
                clip = SourceClip.objects.select_for_update().select_related(
                    "selected_segment__analysis_segment__analysis_run__source_video",
                    "source_asset",
                ).get(pk=clip.pk)
                self._validate_source(clip.selected_segment, clip.source_asset)
                if self.current_input_fingerprint(clip) != clip.input_fingerprint:
                    raise SourceClipError(
                        "Clip inputs changed during processing.", "clip_inputs_changed"
                    )
                clip.processed_asset = processed_asset
                clip.status = SourceClip.Status.VALIDATED
                clip.actual_start_seconds = actual_start
                clip.actual_end_seconds = actual_end
                clip.actual_duration_seconds = processed_asset.duration_seconds
                clip.validation_result = validation_result
                clip.validated_at = timezone.now()
                clip.full_clean()
                clip.save()
                selection_fingerprint = self._selection_fingerprint(clip.selected_segment)
                ArtifactDependencyService.link(
                    clip.project,
                    clip.selected_segment,
                    processed_asset,
                    user,
                    upstream_version=clip.selected_segment.analysis_segment.analysis_run.version,
                    upstream_fingerprint=selection_fingerprint,
                    downstream_version=processed_asset.version,
                    relation_type="trim_selection",
                )
                ArtifactDependencyService.link(
                    clip.project,
                    source_asset,
                    processed_asset,
                    user,
                    upstream_version=source_asset.version,
                    upstream_fingerprint=source_asset.checksum_sha256,
                    downstream_version=processed_asset.version,
                    relation_type="trim_source",
                )
                PipelineJobService.succeed(job)
            clip.refresh_from_db()
            return clip, True
        except (FFmpegError, MediaAssetValidationError, SourceClipError) as exc:
            self._record_failure(clip, job, getattr(exc, "code", "clip_failed"), str(exc), processed_asset)
            raise SourceClipError(str(exc), getattr(exc, "code", "clip_failed")) from exc
        except Exception as exc:
            self._record_failure(
                clip,
                job,
                "clip_processing_failed",
                "Clip processing failed safely.",
                processed_asset,
            )
            raise SourceClipError(
                "Clip processing failed safely.", "clip_processing_failed"
            ) from exc
        finally:
            try:
                os.unlink(output_path)
            except FileNotFoundError:
                pass

    @staticmethod
    def _validate_output(asset, expected_duration):
        if not asset.has_video:
            raise SourceClipError("The processed clip has no video stream.", "clip_video_missing")
        if asset.duration_seconds is None:
            raise SourceClipError("The processed clip has no measured duration.", "clip_duration_missing")
        tolerance = Decimal(str(settings.SOURCE_CLIP_DURATION_TOLERANCE_SECONDS))
        if abs(asset.duration_seconds - expected_duration) > tolerance:
            raise SourceClipError(
                "The processed clip duration is outside tolerance.",
                "clip_duration_mismatch",
            )
        if not asset.width or not asset.height:
            raise SourceClipError("The processed clip has no valid resolution.", "clip_resolution_missing")
        if asset.width > settings.SOURCE_CLIP_MAX_WIDTH or asset.height > settings.SOURCE_CLIP_MAX_HEIGHT:
            raise SourceClipError("The processed clip resolution is unsupported.", "clip_resolution_unsupported")
        if (
            asset.frame_rate is None
            or asset.frame_rate <= 0
            or asset.frame_rate > Decimal(str(settings.SOURCE_CLIP_MAX_FRAME_RATE))
        ):
            raise SourceClipError("The processed clip frame rate is unsupported.", "clip_frame_rate_unsupported")

    @staticmethod
    def _record_failure(clip, job, code, message, processed_asset=None):
        safe_code = str(code)[:64]
        safe_message = str(message)[:255]
        SourceClip.objects.filter(pk=clip.pk).update(
            status=SourceClip.Status.FAILED,
            error_code=safe_code,
            error_message=safe_message,
            updated_at=timezone.now(),
        )
        if processed_asset:
            MediaAsset.objects.filter(pk=processed_asset.pk).update(
                status=MediaAsset.Status.FAILED,
                error_code=safe_code,
                error_message=safe_message,
                updated_at=timezone.now(),
            )
        try:
            PipelineJobService.fail(job, safe_code, safe_message)
        except PipelineJobStateError:
            pass

    @classmethod
    def invalidate_selection_outputs(cls, selection, user):
        ensure_production_allowed(selection.project, user)
        current_selection_fingerprint = cls._selection_fingerprint(selection)
        ArtifactDependencyService.mark_media_dependents_stale(
            selection.project,
            selection,
            current_selection_fingerprint,
            user,
        )
        stale_ids = []
        clips = SourceClip.objects.filter(
            selected_segment=selection,
            status__in=[SourceClip.Status.VALIDATED, SourceClip.Status.APPROVED],
        ).select_related(
            "selected_segment__analysis_segment__analysis_run__source_video",
            "source_asset",
        )
        for clip in clips:
            if cls.is_stale(clip):
                stale_ids.append(clip.pk)
        if stale_ids:
            SourceClip.objects.filter(pk__in=stale_ids).update(
                status=SourceClip.Status.STALE,
                updated_at=timezone.now(),
            )
            from editorial.services.research import invalidate_clip_editorial_outputs

            for stale_clip in SourceClip.objects.filter(pk__in=stale_ids):
                invalidate_clip_editorial_outputs(stale_clip)
        return len(stale_ids)

    @classmethod
    def approve(cls, clip, user):
        stale = False
        with transaction.atomic():
            clip = SourceClip.objects.select_for_update().select_related(
                "project",
                "processed_asset",
                "source_asset",
                "selected_segment__analysis_segment__analysis_run__source_video",
            ).get(pk=clip.pk)
            ensure_production_allowed(clip.project, user)
            if clip.status != SourceClip.Status.VALIDATED:
                raise SourceClipError("Only validated clips can be approved.")
            if cls.is_stale(clip):
                clip.status = SourceClip.Status.STALE
                clip.processed_asset.status = MediaAsset.Status.STALE
                clip.processed_asset.save(update_fields=["status", "updated_at"])
                clip.save(update_fields=["status", "updated_at"])
                stale = True
            else:
                older = list(
                    SourceClip.objects.filter(
                        selected_segment=clip.selected_segment,
                        status=SourceClip.Status.APPROVED,
                    )
                    .exclude(pk=clip.pk)
                    .select_related("processed_asset")
                )
                for previous in older:
                    previous.status = SourceClip.Status.SUPERSEDED
                    previous.save(update_fields=["status", "updated_at"])
                    from editorial.services.research import invalidate_clip_editorial_outputs

                    invalidate_clip_editorial_outputs(previous)
                MediaAssetService.approve(clip.processed_asset, user)
                clip.status = SourceClip.Status.APPROVED
                clip.approved_by = user
                clip.approved_at = timezone.now()
                clip.full_clean()
                clip.save(
                    update_fields=["status", "approved_by", "approved_at", "updated_at"]
                )
        if stale:
            raise SourceClipError("The clip inputs changed and it must be regenerated.")
        clip.refresh_from_db()
        return clip
