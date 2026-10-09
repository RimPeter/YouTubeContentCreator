from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from scraper.models import SourceVideo
from scraper.services import lock_project
from production.services.jobs import PipelineJobService, PipelineJobStateError
from .analysis import AnalysisService, AnalysisServiceError
from .fingerprinting import fingerprint_source_video
from .providers import OpenAITranscriptAnalysisProvider
from .validation import normalize_configuration


class AnalysisJobService:
    @staticmethod
    def enqueue(source, user, configuration):
        with transaction.atomic():
            project = lock_project(source.project_id)
            source = SourceVideo.objects.get(pk=source.pk, project=project)
            source.project = project
            AnalysisService.ensure_analysis_allowed(source, user)
            return PipelineJobService.enqueue(
                project, "transcript_analysis", user,
                input_snapshot={"source_id": source.pk, "source_fingerprint": fingerprint_source_video(source)},
                configuration={"model": settings.OPENAI_ANALYSIS_MODEL,
                               "analysis": normalize_configuration(configuration), "version": "topic-segmentation-v1"},
                max_attempts=2, reuse_completed=False,
            )[0]

    @staticmethod
    def process_job(job, provider=None):
        try:
            PipelineJobService.update_progress(job, 5)
            user = get_user_model().objects.get(pk=job.requested_by_id, is_active=True)
            source = SourceVideo.objects.select_related("project").get(
                pk=job.input_snapshot["source_id"], project_id=job.project_id)
            provider = provider or OpenAITranscriptAnalysisProvider(job.configuration_snapshot["model"])
            return AnalysisService(provider).analyze(source, user, job.configuration_snapshot["analysis"], job=job)
        except Exception as exc:
            message = str(exc) if isinstance(exc, AnalysisServiceError) else "Analysis could not complete. Review the job and retry."
            try:
                PipelineJobService.fail(job, "analysis_failed", message)
            except PipelineJobStateError:
                pass
            raise AnalysisServiceError(message) from None
