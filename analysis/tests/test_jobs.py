from datetime import timedelta
from unittest.mock import Mock

from django.test import TestCase, override_settings
from django.utils import timezone

from analysis.models import AnalysisRun
from analysis.services.analysis import AnalysisService, AnalysisServiceError
from analysis.services.jobs import AnalysisJobService
from analysis.services.provider_errors import AnalysisProviderError
from analysis.tests.helpers import create_approved_source, FakeProvider, valid_provider_output
from production.services.jobs import PipelineJobService


@override_settings(OPENAI_API_KEY="test", OPENAI_ANALYSIS_MODEL="test-model", PIPELINE_RETRY_DELAY_SECONDS=0)
class AnalysisJobTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, self.chunks = create_approved_source("queued-analysis")

    def enqueue(self):
        return AnalysisJobService.enqueue(self.source, self.user, {})

    def test_active_requests_reuse_job_and_explicit_rerun_creates_new_work(self):
        job = self.enqueue()
        self.assertEqual(self.enqueue().pk, job.pk)
        attempt = PipelineJobService.claim_next()
        run = AnalysisJobService.process_job(attempt, FakeProvider([valid_provider_output()]))
        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.result_snapshot["analysis_run_id"], run.pk)
        self.assertNotEqual(self.enqueue().pk, job.pk)

    def test_cancellation_during_provider_call_does_not_publish(self):
        attempt = PipelineJobService.start(self.enqueue())
        provider = FakeProvider([valid_provider_output()])
        original = provider.analyze
        def cancel(*args):
            PipelineJobService.cancel(attempt, self.user)
            return original(*args)
        provider.analyze = cancel
        with self.assertRaises(AnalysisServiceError):
            AnalysisJobService.process_job(attempt, provider)
        self.assertFalse(AnalysisRun.objects.exists())
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "cancelled")

    def test_changed_inputs_are_rejected_before_provider_call(self):
        attempt = PipelineJobService.start(self.enqueue())
        self.chunks[0].text = "Changed"
        self.chunks[0].save(update_fields=["text"])
        provider = Mock()
        with self.assertRaises(AnalysisServiceError):
            AnalysisJobService.process_job(attempt, provider)
        provider.analyze.assert_not_called()
        self.assertFalse(AnalysisRun.objects.exists())

    def test_expired_worker_is_recovered_and_old_attempt_cannot_publish(self):
        attempt = PipelineJobService.start(self.enqueue())
        type(attempt).objects.filter(pk=attempt.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(PipelineJobService.recover_expired(), 1)
        replacement = PipelineJobService.claim_next()
        self.assertNotEqual(attempt.lease_token, replacement.lease_token)
        with self.assertRaises(AnalysisServiceError):
            AnalysisJobService.process_job(attempt, FakeProvider([valid_provider_output()]))
        run = AnalysisJobService.process_job(replacement, FakeProvider([valid_provider_output()]))
        self.assertEqual(AnalysisRun.objects.count(), 1)
        self.assertEqual(run.status, "succeeded")

    def test_authentication_failure_is_not_retried_and_is_explained(self):
        provider = FakeProvider([AnalysisProviderError("authentication", "Check API credentials.")])
        with self.assertLogs("analysis.services.analysis", level="WARNING") as logs:
            run = AnalysisService(provider).analyze(self.source, self.user, {"max_provider_attempts": 3})
        self.assertEqual(provider.calls, 1)
        self.assertTrue(run.used_fallback)
        self.assertIn("authentication", run.error_message)
        self.assertIn("authentication", logs.output[0])

    def test_deactivated_requester_cannot_publish_late_result(self):
        attempt = PipelineJobService.start(self.enqueue())
        provider = FakeProvider([valid_provider_output()])
        original = provider.analyze
        def deactivate(*args):
            type(self.user).objects.filter(pk=self.user.pk).update(is_active=False)
            return original(*args)
        provider.analyze = deactivate
        with self.assertRaises(AnalysisServiceError):
            AnalysisJobService.process_job(attempt, provider)
        self.assertFalse(AnalysisRun.objects.exists())
