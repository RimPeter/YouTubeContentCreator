import re

from django.db import IntegrityError, transaction
from django.utils import timezone

from production.models import PipelineJob
from scraper.models import VideoProject

from .access import ProductionValidationError, ensure_project_access, ensure_production_allowed
from .fingerprints import fingerprint_json


class PipelineJobStateError(ProductionValidationError):
    pass


def _sanitize_error(code, message):
    code = str(code or "job_failed").lower()
    code = re.sub(r"[^a-z0-9_]+", "_", code).strip("_")[:64] or "job_failed"
    message = " ".join(str(message or "The production job failed.").split())[:255]
    return code, message


class PipelineJobService:
    @staticmethod
    def enqueue(
        project,
        job_type,
        user,
        *,
        input_snapshot,
        configuration=None,
        input_fingerprint=None,
        max_attempts=3,
    ):
        ensure_production_allowed(project, user)
        configuration = configuration or {}
        input_fingerprint = input_fingerprint or fingerprint_json(input_snapshot)
        if not re.fullmatch(r"[0-9a-f]{64}", input_fingerprint):
            raise ProductionValidationError("Input fingerprint must be a SHA-256 digest.")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(job_type)):
            raise ProductionValidationError("Job type must be a lowercase identifier.")
        if not 1 <= int(max_attempts) <= 20:
            raise ProductionValidationError("Maximum attempts must be between 1 and 20.")
        idempotency_key = fingerprint_json(
            {
                "job_type": job_type,
                "input_fingerprint": input_fingerprint,
                "configuration": configuration,
            }
        )
        try:
            with transaction.atomic():
                locked_project = VideoProject.objects.select_for_update().get(pk=project.pk)
                ensure_production_allowed(locked_project, user)
                existing = (
                    PipelineJob.objects.filter(
                        project=locked_project,
                        job_type=job_type,
                        idempotency_key=idempotency_key,
                        status__in=[
                            PipelineJob.Status.QUEUED,
                            PipelineJob.Status.RUNNING,
                            PipelineJob.Status.SUCCEEDED,
                        ],
                    )
                    .order_by("-created_at")
                    .first()
                )
                if existing:
                    return existing, False
                job = PipelineJob(
                    project=locked_project,
                    job_type=job_type,
                    idempotency_key=idempotency_key,
                    requested_by=user,
                    max_attempts=int(max_attempts),
                    input_fingerprint=input_fingerprint,
                    input_snapshot=input_snapshot,
                    configuration_snapshot=configuration,
                )
                job.full_clean()
                job.save()
                return job, True
        except IntegrityError:
            existing = PipelineJob.objects.filter(
                project=project,
                job_type=job_type,
                idempotency_key=idempotency_key,
                status__in=[PipelineJob.Status.QUEUED, PipelineJob.Status.RUNNING],
            ).first()
            if existing:
                return existing, False
            raise

    @staticmethod
    def start(job):
        with transaction.atomic():
            job = PipelineJob.objects.select_for_update().get(pk=job.pk)
            if job.status != PipelineJob.Status.QUEUED:
                raise PipelineJobStateError("Only queued jobs can start.")
            if job.attempt_count >= job.max_attempts:
                raise PipelineJobStateError("This job has exhausted its attempts.")
            job.status = PipelineJob.Status.RUNNING
            job.attempt_count += 1
            job.started_at = timezone.now()
            job.completed_at = None
            job.cancelled_at = None
            job.error_code = ""
            job.error_message = ""
            job.full_clean()
            job.save()
            return job

    @staticmethod
    def update_progress(job, progress):
        try:
            progress = int(progress)
        except (TypeError, ValueError) as exc:
            raise PipelineJobStateError("Progress must be an integer.") from exc
        if not 0 <= progress <= 99:
            raise PipelineJobStateError("Running job progress must be between 0 and 99.")
        with transaction.atomic():
            job = PipelineJob.objects.select_for_update().get(pk=job.pk)
            if job.status != PipelineJob.Status.RUNNING:
                raise PipelineJobStateError("Only running jobs can report progress.")
            if progress < job.progress:
                raise PipelineJobStateError("Job progress cannot move backwards.")
            job.progress = progress
            job.save(update_fields=["progress", "updated_at"])
            return job

    @staticmethod
    def succeed(job):
        with transaction.atomic():
            job = PipelineJob.objects.select_for_update().get(pk=job.pk)
            if job.status != PipelineJob.Status.RUNNING:
                raise PipelineJobStateError("Only running jobs can succeed.")
            job.status = PipelineJob.Status.SUCCEEDED
            job.progress = 100
            job.completed_at = timezone.now()
            job.full_clean()
            job.save()
            return job

    @staticmethod
    def fail(job, code="job_failed", message="The production job failed."):
        with transaction.atomic():
            job = PipelineJob.objects.select_for_update().get(pk=job.pk)
            if job.status != PipelineJob.Status.RUNNING:
                raise PipelineJobStateError("Only running jobs can fail.")
            job.status = PipelineJob.Status.FAILED
            job.completed_at = timezone.now()
            job.error_code, job.error_message = _sanitize_error(code, message)
            job.full_clean()
            job.save()
            return job

    @staticmethod
    def retry(job, user):
        with transaction.atomic():
            job = PipelineJob.objects.select_for_update().select_related("project").get(pk=job.pk)
            ensure_production_allowed(job.project, user)
            if job.status != PipelineJob.Status.FAILED:
                raise PipelineJobStateError("Only failed jobs can be retried.")
            if job.attempt_count >= job.max_attempts:
                raise PipelineJobStateError("This job has exhausted its attempts.")
            job.status = PipelineJob.Status.QUEUED
            job.progress = 0
            job.completed_at = None
            job.error_code = ""
            job.error_message = ""
            job.full_clean()
            job.save()
            return job

    @staticmethod
    def cancel(job, user):
        with transaction.atomic():
            job = PipelineJob.objects.select_for_update().select_related("project").get(pk=job.pk)
            ensure_project_access(job.project, user)
            if job.status not in {PipelineJob.Status.QUEUED, PipelineJob.Status.RUNNING}:
                raise PipelineJobStateError("Only queued or running jobs can be cancelled.")
            job.status = PipelineJob.Status.CANCELLED
            job.cancelled_at = timezone.now()
            job.full_clean()
            job.save()
            return job
