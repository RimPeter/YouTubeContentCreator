import logging

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from analysis.models import AnalysisRun, AnalysisSegment
from scraper.models import SourceVideo, VideoProject
from scraper.services import lock_project
from .provider_errors import AnalysisProviderError

logger = logging.getLogger(__name__)

from .fingerprinting import fingerprint_chunks, fingerprint_source_video, ordered_transcript_chunks
from .validation import (
    AnalysisOutputValidationError,
    build_fallback_output,
    normalize_configuration,
    validate_provider_output,
)


ALGORITHM_VERSION = "topic-segmentation-v1"


class AnalysisServiceError(Exception):
    pass


class AnalysisInputError(AnalysisServiceError):
    pass


class AnalysisPermissionError(AnalysisServiceError):
    pass


class AnalysisPersistenceError(AnalysisServiceError):
    pass


class AnalysisService:
    def __init__(self, provider=None):
        self.provider = provider

    @staticmethod
    def delete_run(run, user):
        from analysis.models import SegmentSelection
        from production.models import PipelineJob
        with transaction.atomic():
            project = lock_project(run.source_video.project_id)
            if not user.is_active or not (project.owner_id == user.pk or user.is_staff or user.is_superuser):
                raise AnalysisPermissionError("You cannot delete this analysis version.")
            if project.is_locked or project.status == VideoProject.Status.ARCHIVED:
                raise AnalysisInputError("Unlock the project before deleting versions; archived projects are read-only.")
            current = AnalysisRun.objects.filter(pk=run.pk, source_video__project=project).first()
            if current is None:
                raise AnalysisInputError("This analysis version has already been deleted.")
            if current.status == AnalysisRun.Status.RUNNING:
                raise AnalysisInputError("A running analysis cannot be deleted.")
            if SegmentSelection.objects.filter(analysis_segment__analysis_run=current).exists():
                raise AnalysisInputError("This version is referenced by current or past segment selections and cannot be deleted.")
            # Keep job history, but remove links to the deleted result.
            for job in PipelineJob.objects.filter(project=project, result_snapshot__analysis_run_id=current.pk):
                result = dict(job.result_snapshot)
                result.pop("analysis_run_id", None)
                result["analysis_deleted"] = True
                job.result_snapshot = result
                job.save(update_fields=["result_snapshot"])
            current.delete()

    @staticmethod
    def ensure_analysis_allowed(source_video, user):
        project = source_video.project
        if not user or not user.is_active or not user.is_authenticated or (
            project.owner_id != user.pk and not user.is_staff and not user.is_superuser
        ):
            raise AnalysisPermissionError("You cannot analyze this project's transcripts.")
        if (
            project.status != VideoProject.Status.APPROVED
            or project.is_locked
            or project.status == VideoProject.Status.ARCHIVED
        ):
            raise AnalysisInputError(
                "Analysis requires an approved, unlocked, non-archived project."
            )
        if source_video.transcript_status != SourceVideo.TranscriptStatus.COMPLETED:
            raise AnalysisInputError("Analysis requires a completed transcript.")
        if not any(
            chunk.text.strip()
            for chunk in source_video.transcript_chunks.only("text").iterator()
        ):
            raise AnalysisInputError("Analysis requires at least one non-empty transcript chunk.")

    @staticmethod
    def transcript_payload(chunks):
        return [
            {
                "sequence": chunk.sequence,
                "start_seconds": chunk.start_seconds,
                "duration_seconds": chunk.duration_seconds,
                "text": chunk.text,
            }
            for chunk in chunks
        ]

    def _create_running_run(self, source_video, user, configuration, fingerprint):
        for attempt in range(2):
            try:
                with transaction.atomic():
                    try:
                        project = lock_project(source_video.project_id)
                        locked_source = SourceVideo.objects.get(pk=source_video.pk, project=project)
                    except (VideoProject.DoesNotExist, SourceVideo.DoesNotExist) as exc:
                        raise AnalysisInputError("The analysis source no longer exists.") from exc
                    locked_source.project = project
                    self.ensure_analysis_allowed(locked_source, user)
                    if fingerprint_source_video(locked_source) != fingerprint:
                        raise AnalysisInputError("The transcript changed before analysis started. Try again.")
                    latest_version = (
                        AnalysisRun.objects.filter(source_video=locked_source).aggregate(
                            latest=Max("version")
                        )["latest"]
                        or 0
                    )
                    return AnalysisRun.objects.create(
                        source_video=locked_source,
                        version=latest_version + 1,
                        status=AnalysisRun.Status.RUNNING,
                        source_fingerprint=fingerprint,
                        provider=getattr(self.provider, "name", "") if self.provider else "",
                        model=getattr(self.provider, "model", "") if self.provider else "",
                        prompt_version=(
                            getattr(self.provider, "prompt_version", "") if self.provider else ""
                        ),
                        algorithm_version=ALGORITHM_VERSION,
                        configuration=configuration,
                        created_by=user,
                    )
            except IntegrityError:
                if attempt:
                    raise AnalysisPersistenceError("Could not allocate an analysis version.")
        raise AnalysisPersistenceError("Could not allocate an analysis version.")

    def analyze(self, source_video, user, configuration=None, *, job=None):
        from production.services.jobs import PipelineJobService
        self.ensure_analysis_allowed(source_video, user)
        try:
            normalized_configuration = normalize_configuration(configuration)
        except AnalysisOutputValidationError as exc:
            raise AnalysisInputError(str(exc)) from exc
        chunks = ordered_transcript_chunks(source_video)
        fingerprint = fingerprint_chunks(chunks)
        if job and fingerprint != job.input_snapshot["source_fingerprint"]:
            raise AnalysisInputError("The transcript changed; request a new analysis.")
        run = None if job else self._create_running_run(
            source_video,
            user,
            normalized_configuration,
            fingerprint,
        )

        validated_segments = None
        provider_failed = False
        provider_error = None
        if self.provider is not None:
            payload = self.transcript_payload(chunks)
            for _attempt in range(normalized_configuration["max_provider_attempts"]):
                if job:
                    PipelineJobService.update_progress(job, 10 + _attempt * 20)
                try:
                    output = self.provider.analyze(payload, normalized_configuration)
                    validated_segments = validate_provider_output(
                        output,
                        chunks,
                        normalized_configuration["score_weights"],
                    )
                    break
                except Exception as exc:
                    provider_failed = True
                    provider_error = exc if isinstance(exc, AnalysisProviderError) else AnalysisProviderError(
                        "invalid_output" if isinstance(exc, AnalysisOutputValidationError) else "provider_error",
                        "Provider output failed validation." if isinstance(exc, AnalysisOutputValidationError)
                        else "The analysis provider failed.",
                        retryable=isinstance(exc, AnalysisOutputValidationError),
                    )
                    logger.warning("Analysis provider failure: source=%s category=%s attempt=%s",
                                   source_video.pk, provider_error.code, _attempt + 1)
                    if not provider_error.retryable:
                        break

        used_fallback = validated_segments is None
        if used_fallback:
            try:
                fallback = build_fallback_output(
                    chunks,
                    normalized_configuration["fallback_max_chunks"],
                )
                validated_segments = validate_provider_output(
                    fallback,
                    chunks,
                    normalized_configuration["score_weights"],
                )
            except Exception as exc:
                AnalysisRun.objects.filter(pk=run.pk if run else None).update(
                    status=AnalysisRun.Status.FAILED,
                    completed_at=timezone.now(),
                    error_code="analysis_failed",
                    error_message="Analysis and deterministic fallback both failed.",
                )
                raise AnalysisServiceError("Analysis could not produce valid segments.") from exc

        try:
            with transaction.atomic():
                try:
                    project = lock_project(source_video.project_id)
                    current_source = SourceVideo.objects.get(pk=source_video.pk, project=project)
                except (VideoProject.DoesNotExist, SourceVideo.DoesNotExist) as exc:
                    raise AnalysisInputError("The analysis source no longer exists.") from exc
                current_source.project = project
                if job:
                    from django.contrib.auth import get_user_model
                    user = get_user_model().objects.filter(pk=job.requested_by_id, is_active=True).first()
                self.ensure_analysis_allowed(current_source, user)
                if fingerprint_source_video(current_source) != fingerprint:
                    raise AnalysisInputError("The transcript changed during analysis. Run analysis again.")
                if job:
                    PipelineJobService._running(job)
                    run = self._create_running_run(current_source, user, normalized_configuration, fingerprint)
                AnalysisSegment.objects.bulk_create(
                    [
                        AnalysisSegment(
                            analysis_run=run,
                            order=segment.order,
                            start_chunk=segment.start_chunk,
                            end_chunk=segment.end_chunk,
                            start_seconds=segment.start_seconds,
                            end_seconds=segment.end_seconds,
                            title=segment.title,
                            summary=segment.summary,
                            source_text=segment.source_text,
                            topic_labels=segment.topic_labels,
                            component_scores=segment.component_scores,
                            aggregate_score=segment.aggregate_score,
                            rationale=segment.rationale,
                            editorial_recommendation=segment.editorial_recommendation,
                        )
                        for segment in validated_segments
                    ]
                )
                run.status = AnalysisRun.Status.SUCCEEDED
                run.used_fallback = used_fallback
                run.completed_at = timezone.now()
                if provider_failed and used_fallback:
                    run.error_code = "provider_fallback"
                    run.error_message = f"{provider_error.code}: {provider_error} Deterministic fallback was used."
                run.save(
                    update_fields=[
                        "status",
                        "used_fallback",
                        "completed_at",
                        "error_code",
                        "error_message",
                    ]
                )
                if job:
                    PipelineJobService.succeed(job, {"analysis_run_id": run.pk, "warning": run.error_message})
        except (AnalysisInputError, AnalysisPermissionError) as exc:
            AnalysisRun.objects.filter(pk=run.pk if run and not job else None).update(
                status=AnalysisRun.Status.FAILED,
                completed_at=timezone.now(),
                error_code="input_changed",
                error_message=str(exc),
            )
            raise
        except Exception as exc:
            logger.error("Analysis persistence failure: source=%s", source_video.pk)
            AnalysisRun.objects.filter(pk=run.pk if run and not job else None).update(
                status=AnalysisRun.Status.FAILED,
                completed_at=timezone.now(),
                error_code="persistence_failed",
                error_message="Validated analysis could not be saved.",
            )
            raise AnalysisPersistenceError("Validated analysis could not be saved.") from exc
        return run

    @staticmethod
    def is_stale(run):
        return run.source_fingerprint != fingerprint_source_video(run.source_video)
