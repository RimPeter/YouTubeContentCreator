import re
import uuid
import threading
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connections, transaction
from django.db.models import Q
from django.utils import timezone

from production.models import MediaAsset, PipelineJob, SourceClip
from scraper.services import lock_project
from .access import ProductionServiceError, ProductionValidationError, ensure_project_access, ensure_production_allowed
from .fingerprints import fingerprint_json


class PipelineJobStateError(ProductionValidationError):
    pass


def _sanitize_error(code, message):
    code = re.sub(r"[^a-z0-9_]+", "_", str(code or "job_failed").lower()).strip("_")[:64]
    return code or "job_failed", " ".join(str(message or "The production job failed.").split())[:255]


class PipelineJobService:
    """Project-serialized job records with a fenced lease for each attempt."""

    @staticmethod
    def enqueue(project, job_type, user, *, input_snapshot, configuration=None,
                input_fingerprint=None, max_attempts=3, reuse_completed=True):
        configuration = configuration or {}
        input_fingerprint = input_fingerprint or fingerprint_json(input_snapshot)
        if not re.fullmatch(r"[0-9a-f]{64}", input_fingerprint):
            raise ProductionValidationError("Input fingerprint must be a SHA-256 digest.")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(job_type)):
            raise ProductionValidationError("Job type must be a lowercase identifier.")
        try:
            max_attempts = int(max_attempts)
        except (ValueError, TypeError) as exc:
            raise ProductionValidationError("Maximum attempts must be an integer.") from exc
        if not 1 <= max_attempts <= 20:
            raise ProductionValidationError("Maximum attempts must be between 1 and 20.")
        key = fingerprint_json({"job_type": job_type, "input_fingerprint": input_fingerprint,
                                "configuration": configuration})
        fingerprint_json(input_snapshot)
        with transaction.atomic():
            project = lock_project(project.pk)
            ensure_production_allowed(project, user)
            statuses = [PipelineJob.Status.QUEUED, PipelineJob.Status.RUNNING]
            if reuse_completed:
                statuses.append(PipelineJob.Status.SUCCEEDED)
            existing = PipelineJob.objects.filter(
                project=project, job_type=job_type, idempotency_key=key, status__in=statuses,
            ).order_by("-created_at").first()
            if existing:
                return existing, False
            job = PipelineJob(project=project, job_type=job_type, idempotency_key=key,
                              requested_by=user, max_attempts=max_attempts,
                              input_fingerprint=input_fingerprint, input_snapshot=input_snapshot,
                              configuration_snapshot=configuration)
            job.full_clean()
            job.save()
            return job, True

    @staticmethod
    def _lease_duration():
        return timedelta(seconds=max(5, int(getattr(settings, "PIPELINE_LEASE_SECONDS", 30))))

    @classmethod
    def _running(cls, attempt):
        """Called inside the project's transaction. Never adopt a newer token."""
        job = PipelineJob.objects.get(pk=attempt.pk)
        if (job.status != PipelineJob.Status.RUNNING or not attempt.lease_token
                or job.lease_token != attempt.lease_token
                or not job.lease_expires_at or job.lease_expires_at <= timezone.now()):
            raise PipelineJobStateError("This processing attempt is no longer active.")
        return job

    @classmethod
    def start(cls, job):
        with transaction.atomic():
            project = lock_project(job.project_id)
            job = PipelineJob.objects.get(pk=job.pk)
            user = get_user_model().objects.filter(pk=job.requested_by_id, is_active=True).first()
            ensure_production_allowed(project, user)
            if job.status != PipelineJob.Status.QUEUED or job.available_at > timezone.now():
                raise PipelineJobStateError("Only available queued jobs can start.")
            if job.attempt_count >= job.max_attempts:
                raise PipelineJobStateError("This job has exhausted its attempts.")
            job.status = PipelineJob.Status.RUNNING
            job.attempt_count += 1
            job.started_at = job.heartbeat_at = timezone.now()
            job.lease_token = uuid.uuid4()
            job.lease_expires_at = job.heartbeat_at + cls._lease_duration()
            job.completed_at = job.cancelled_at = None
            job.error_code = job.error_message = ""
            job.save()
            return job

    @classmethod
    def heartbeat(cls, attempt):
        with transaction.atomic():
            project = lock_project(attempt.project_id)
            job = cls._running(attempt)
            user = get_user_model().objects.filter(pk=job.requested_by_id, is_active=True).first()
            ensure_production_allowed(project, user)
            job.heartbeat_at = timezone.now()
            job.lease_expires_at = job.heartbeat_at + cls._lease_duration()
            job.save(update_fields=["heartbeat_at", "lease_expires_at", "updated_at"])
            return job

    @classmethod
    @contextmanager
    def maintain_lease(cls, attempt):
        """Worker-only heartbeat covering hashing, probing, and storage as well as FFmpeg.

        The claim must already be committed so the heartbeat's connection can
        see it. Synchronous callers use the inline processing checkpoints.
        """
        stopped = threading.Event()
        interval = max(0.1, min(float(getattr(settings, "PIPELINE_HEARTBEAT_SECONDS", 5)),
                               cls._lease_duration().total_seconds() / 3))

        def beat():
            try:
                while not stopped.wait(interval):
                    close_old_connections()
                    try:
                        cls.heartbeat(attempt)
                    except OperationalError:
                        # A short SQLite writer may hold the lock. Expiry and
                        # final token checks still prevent publishing late work.
                        continue
                    except ProductionServiceError:
                        return
            finally:
                connections.close_all()

        thread = threading.Thread(target=beat, name=f"pipeline-lease-{attempt.pk}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=10)

    @classmethod
    def update_progress(cls, attempt, progress):
        try:
            progress = int(progress)
        except (TypeError, ValueError) as exc:
            raise PipelineJobStateError("Progress must be an integer.") from exc
        if not 0 <= progress <= 99:
            raise PipelineJobStateError("Running job progress must be between 0 and 99.")
        with transaction.atomic():
            lock_project(attempt.project_id)
            job = cls._running(attempt)
            if progress < job.progress:
                raise PipelineJobStateError("Job progress cannot move backwards.")
            job.progress = progress
            job.save(update_fields=["progress", "updated_at"])
            return job

    @classmethod
    def succeed(cls, attempt, result=None):
        with transaction.atomic():
            lock_project(attempt.project_id)
            job = cls._running(attempt)
            job.status = PipelineJob.Status.SUCCEEDED
            job.progress = 100
            if result is not None:
                job.result_snapshot = result
            job.completed_at = timezone.now()
            job.lease_expires_at = None
            job.save()
            return job

    @classmethod
    def fail(cls, attempt, code="job_failed", message="The production job failed."):
        with transaction.atomic():
            lock_project(attempt.project_id)
            job = cls._running(attempt)
            job.status = PipelineJob.Status.FAILED
            job.completed_at = timezone.now()
            job.lease_expires_at = None
            job.error_code, job.error_message = _sanitize_error(code, message)
            job.save()
            return job

    @staticmethod
    def retry(job, user):
        with transaction.atomic():
            project = lock_project(job.project_id)
            ensure_production_allowed(project, user)
            job = PipelineJob.objects.get(pk=job.pk)
            if job.status != PipelineJob.Status.FAILED:
                raise PipelineJobStateError("Only failed jobs can be retried.")
            if job.attempt_count >= job.max_attempts:
                raise PipelineJobStateError("This job has exhausted its attempts.")
            if PipelineJob.objects.filter(
                project=project, job_type=job.job_type, idempotency_key=job.idempotency_key,
                status__in=[PipelineJob.Status.QUEUED, PipelineJob.Status.RUNNING],
            ).exclude(pk=job.pk).exists():
                raise PipelineJobStateError("Equivalent work is already queued or running; follow that job instead.")
            clip = SourceClip.objects.filter(pipeline_job=job).first()
            if clip:
                from .clips import SourceClipService
                if clip.status != SourceClip.Status.FAILED or SourceClipService.is_stale(clip):
                    raise PipelineJobStateError("Clip inputs changed; create a new clip instead.")
                clip.status = SourceClip.Status.PROCESSING
                clip.error_code = clip.error_message = ""
                clip.save(update_fields=["status", "error_code", "error_message", "updated_at"])
            job.status = PipelineJob.Status.QUEUED
            job.progress = 0
            job.available_at = timezone.now()
            job.completed_at = job.lease_token = job.lease_expires_at = None
            job.error_code = job.error_message = ""
            job.save()
            return job

    @staticmethod
    def cancel(job, user):
        with transaction.atomic():
            project = lock_project(job.project_id)
            ensure_project_access(project, user)
            job = PipelineJob.objects.get(pk=job.pk)
            if job.status not in {PipelineJob.Status.QUEUED, PipelineJob.Status.RUNNING}:
                raise PipelineJobStateError("Only queued or running jobs can be cancelled.")
            job.status = PipelineJob.Status.CANCELLED
            job.cancelled_at = timezone.now()
            job.lease_token = job.lease_expires_at = None
            job.save()
            SourceClip.objects.filter(pipeline_job=job, status=SourceClip.Status.PROCESSING).update(
                status=SourceClip.Status.FAILED, error_code="job_cancelled",
                error_message="Clip processing was cancelled.", updated_at=timezone.now())
            return job

    @classmethod
    def recover_expired(cls):
        expired = PipelineJob.objects.filter(status=PipelineJob.Status.RUNNING).filter(
            Q(lease_expires_at__lte=timezone.now()) | Q(lease_expires_at__isnull=True)
        ).values_list("pk", "project_id")
        recovered = 0
        for job_id, project_id in list(expired):
            with transaction.atomic():
                lock_project(project_id)
                job = PipelineJob.objects.get(pk=job_id)
                if job.status != PipelineJob.Status.RUNNING:
                    continue
                if job.lease_expires_at and job.lease_expires_at > timezone.now():
                    continue
                previous_token = str(job.lease_token)
                MediaAsset.objects.filter(
                    configuration_snapshot__pipeline_job_id=job.pk,
                    configuration_snapshot__attempt_token=previous_token,
                    source_clip_output__isnull=True, status=MediaAsset.Status.VALIDATED,
                ).update(status=MediaAsset.Status.FAILED, error_code="worker_interrupted",
                         error_message="Output belongs to an interrupted attempt.", updated_at=timezone.now())
                job.status = PipelineJob.Status.FAILED
                job.completed_at = timezone.now()
                job.lease_token = job.lease_expires_at = None
                job.error_code = "worker_interrupted"
                job.error_message = "The worker stopped responding. Retry the job or request new work."
                job.save()
                SourceClip.objects.filter(pipeline_job=job, status=SourceClip.Status.PROCESSING).update(
                    status=SourceClip.Status.FAILED, error_code=job.error_code,
                    error_message=job.error_message, updated_at=timezone.now())
                user = get_user_model().objects.filter(pk=job.requested_by_id, is_active=True).first()
                try:
                    with transaction.atomic():
                        retried = cls.retry(job, user)
                        retried.available_at = timezone.now() + timedelta(
                            seconds=getattr(settings, "PIPELINE_RETRY_DELAY_SECONDS", 5))
                        retried.save(update_fields=["available_at"])
                except ProductionServiceError:
                    pass
                recovered += 1
        return recovered

    @classmethod
    def claim_next(cls):
        candidates = PipelineJob.objects.filter(
            status=PipelineJob.Status.QUEUED, available_at__lte=timezone.now(),
        ).filter(
            Q(job_type="clip_trim", source_clip__isnull=False) | Q(job_type__in=["ai_research", "ai_reaction", "transcript_analysis", "sequence_reaction"])
        ).order_by("available_at", "created_at")
        for job in list(candidates[:20]):
            try:
                return cls.start(job)
            except PipelineJobStateError:
                continue
            except ProductionServiceError:
                with transaction.atomic():
                    lock_project(job.project_id)
                    changed = PipelineJob.objects.filter(pk=job.pk, status=PipelineJob.Status.QUEUED).update(
                        status=PipelineJob.Status.FAILED, completed_at=timezone.now(),
                        error_code="job_inputs_unavailable", error_message="Project or requester is no longer eligible.")
                    if changed:
                        SourceClip.objects.filter(pipeline_job=job, status=SourceClip.Status.PROCESSING).update(
                            status=SourceClip.Status.FAILED, error_code="job_inputs_unavailable",
                            error_message="Project or requester is no longer eligible.")
        return None
