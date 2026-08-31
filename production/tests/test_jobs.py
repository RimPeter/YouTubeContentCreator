from django.contrib.auth import get_user_model
from django.test import TestCase

from production.models import PipelineJob
from production.services.access import (
    ProductionLifecycleError,
    ProductionPermissionError,
    ProductionValidationError,
)
from production.services.jobs import PipelineJobService, PipelineJobStateError

from .helpers import create_approved_project


class PipelineJobServiceTests(TestCase):
    def setUp(self):
        self.user, self.project = create_approved_project("job-owner")
        self.other = get_user_model().objects.create_user(username="job-other")

    def enqueue(self, **overrides):
        values = {
            "input_snapshot": {"selection_id": 1},
            "configuration": {"padding": 0.5},
            "max_attempts": 2,
        }
        values.update(overrides)
        return PipelineJobService.enqueue(
            self.project, "clip_trim", self.user, **values
        )

    def test_enqueue_is_idempotent_and_configuration_changes_identity(self):
        first, created = self.enqueue()
        repeated, repeated_created = self.enqueue()
        changed, changed_created = self.enqueue(configuration={"padding": 1})

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(first.pk, repeated.pk)
        self.assertTrue(changed_created)
        self.assertNotEqual(first.idempotency_key, changed.idempotency_key)

    def test_job_lifecycle_progress_failure_retry_and_success(self):
        job, _ = self.enqueue()
        job = PipelineJobService.start(job)
        self.assertEqual(job.attempt_count, 1)
        job = PipelineJobService.update_progress(job, 25)
        self.assertEqual(job.progress, 25)
        job = PipelineJobService.fail(job, "Provider Secret/Error", " safe   failure ")
        self.assertEqual(job.error_code, "provider_secret_error")
        self.assertEqual(job.error_message, "safe failure")

        job = PipelineJobService.retry(job, self.user)
        job = PipelineJobService.start(job)
        job = PipelineJobService.succeed(job)
        self.assertEqual(job.status, PipelineJob.Status.SUCCEEDED)
        self.assertEqual(job.progress, 100)

        repeated, created = self.enqueue()
        self.assertFalse(created)
        self.assertEqual(repeated.pk, job.pk)

    def test_retry_is_bounded(self):
        job, _ = self.enqueue(max_attempts=1)
        job = PipelineJobService.start(job)
        job = PipelineJobService.fail(job)
        with self.assertRaises(PipelineJobStateError):
            PipelineJobService.retry(job, self.user)

    def test_progress_cannot_reverse_and_cancel_requires_access(self):
        job, _ = self.enqueue()
        job = PipelineJobService.start(job)
        job = PipelineJobService.update_progress(job, 50)
        with self.assertRaises(PipelineJobStateError):
            PipelineJobService.update_progress(job, 25)
        with self.assertRaises(ProductionPermissionError):
            PipelineJobService.cancel(job, self.other)
        job = PipelineJobService.cancel(job, self.user)
        self.assertEqual(job.status, PipelineJob.Status.CANCELLED)

    def test_enqueue_validates_project_lifecycle_and_inputs(self):
        with self.assertRaises(ProductionPermissionError):
            PipelineJobService.enqueue(
                self.project,
                "clip_trim",
                self.other,
                input_snapshot={},
            )
        self.project.is_locked = True
        self.project.locked_at = self.project.approved_at
        self.project.save(update_fields=["is_locked", "locked_at"])
        with self.assertRaises(ProductionLifecycleError):
            self.enqueue()
        self.project.is_locked = False
        self.project.locked_at = None
        self.project.save(update_fields=["is_locked", "locked_at"])
        with self.assertRaises(ProductionValidationError):
            self.enqueue(input_snapshot={"not_json": object()})
