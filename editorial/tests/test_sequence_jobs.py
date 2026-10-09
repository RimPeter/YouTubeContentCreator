from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from django.urls import reverse

from analysis.services import AnalysisService, SelectionService
from analysis.tests.helpers import create_approved_source
from editorial.models import ReactionSequenceDraft
from editorial.services.access import EditorialServiceError
from editorial.services.reaction_sequence_plans import ReactionSequencePlanService
from editorial.services.reaction_sequence_drafts import ReactionSequenceDraftService
from production.models import PipelineJob
from production.services.jobs import PipelineJobService


@override_settings(OPENAI_API_KEY="test", OPENAI_REACTION_MODEL="test-model")
class SequenceJobTests(TestCase):
    def setUp(self):
        self.user, self.project, source, _ = create_approved_source("sequence-job")
        run = AnalysisService().analyze(source, self.user)
        SelectionService.select(self.project, run.segments.first(), self.user)
        self.plan = ReactionSequencePlanService.create(self.project, self.user)
        self.plan = ReactionSequencePlanService.mark_ready(self.plan, self.user, research_reviewed=True)
        self.provider = Mock(provider="test", model="test-model")
        self.provider.generate.return_value = ReactionSequenceDraftService._fallback(self.plan)

    def test_post_enqueues_without_provider_call_and_worker_publishes_result(self):
        self.client.force_login(self.user)
        with patch("editorial.services.reaction_sequence_drafts.OpenAISequenceReactionProvider") as provider:
            for _ in range(2):
                response = self.client.post(reverse("editorial:generate_reaction_sequence_draft", args=[self.plan.pk]))
                self.assertRedirects(response, reverse("production:project_jobs", args=[self.project.pk]))
            provider.assert_not_called()
        self.assertEqual(PipelineJob.objects.filter(job_type="sequence_reaction").count(), 1)
        self.assertFalse(ReactionSequenceDraft.objects.exists())
        job = PipelineJobService.claim_next()
        draft = ReactionSequenceDraftService.process_job(job, self.provider)
        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.result_snapshot["sequence_draft_id"], draft.pk)
        self.assertEqual(ReactionSequenceDraftService.enqueue(self.plan, self.user).pk, job.pk)
        self.assertContains(self.client.get(reverse("production:project_jobs", args=[self.project.pk])),
                            reverse("editorial:reaction_sequence_draft_detail", args=[draft.pk]))

    def test_cancelled_attempt_cannot_publish(self):
        job = PipelineJobService.start(ReactionSequenceDraftService.enqueue(self.plan, self.user))
        def generate(payload):
            PipelineJobService.cancel(job, self.user)
            return ReactionSequenceDraftService._fallback(self.plan)
        self.provider.generate.side_effect = generate
        with self.assertRaises(EditorialServiceError):
            ReactionSequenceDraftService.process_job(job, self.provider)
        self.assertFalse(ReactionSequenceDraft.objects.exists())
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")

    def test_changed_plan_text_rejects_late_result(self):
        job = PipelineJobService.start(ReactionSequenceDraftService.enqueue(self.plan, self.user))
        def generate(payload):
            self.plan.overall_thesis = "Changed during generation"
            self.plan.save(update_fields=["overall_thesis"])
            return self.provider.generate.return_value
        self.provider.generate.side_effect = generate
        with self.assertRaises(EditorialServiceError):
            ReactionSequenceDraftService.process_job(job, self.provider)
        self.assertFalse(ReactionSequenceDraft.objects.exists())
