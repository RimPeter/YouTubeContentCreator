from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from unittest.mock import patch
from django.urls import reverse

from analysis.models import AnalysisRun, AnalysisSegmentReview, SegmentSelection
from scraper.models import VideoProject

from .helpers import create_approved_source


@override_settings(ALLOWED_HOSTS=["testserver"])
@override_settings(OPENAI_API_KEY="")
class AnalysisViewIntegrationTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, self.chunks = create_approved_source(
            username="view-analysis-owner"
        )
        self.other = get_user_model().objects.create_user(
            username="view-analysis-other", password="password"
        )
        self.client.force_login(self.user)

    def run_analysis(self):
        response = self.client.post(
            reverse("analysis:run_analysis", args=[self.source.pk]),
            {"fallback_max_chunks": 2, "max_provider_attempts": 1},
        )
        run = AnalysisRun.objects.get()
        self.assertRedirects(response, reverse("analysis:run_detail", args=[run.pk]))
        return run

    def test_dashboard_run_fallback_select_reorder_and_deselect(self):
        dashboard = self.client.get(
            reverse("analysis:project_dashboard", args=[self.project.pk])
        )
        self.assertContains(dashboard, "Analysis source")
        self.assertEqual(
            self.client.get(reverse("analysis:run_analysis", args=[self.source.pk])).status_code,
            405,
        )
        run = self.run_analysis()
        self.assertTrue(run.used_fallback)
        self.assertEqual(run.segments.count(), 2)
        detail = self.client.get(reverse("analysis:run_detail", args=[run.pk]))
        self.assertContains(detail, "deterministic fallback")

        segments = list(run.segments.order_by("order"))
        for segment in segments:
            self.client.post(
                reverse("analysis:select_segment", args=[segment.pk]),
                {
                    "reviewed_start_seconds": segment.start_seconds,
                    "reviewed_end_seconds": segment.end_seconds,
                    "notes": f"Select {segment.order}",
                },
            )
        selections = list(self.project.segment_selections.order_by("order"))
        self.assertEqual(len(selections), 2)
        self.client.post(
            reverse("analysis:reorder_selections", args=[self.project.pk]),
            {
                f"position_{selections[0].pk}": 2,
                f"position_{selections[1].pk}": 1,
            },
        )
        self.assertEqual(
            list(self.project.segment_selections.values_list("pk", flat=True)),
            [selections[1].pk, selections[0].pk],
        )
        self.client.post(
            reverse("analysis:deselect_segment", args=[selections[1].pk])
        )
        self.assertEqual(SegmentSelection.objects.count(), 2)
        self.assertEqual(SegmentSelection.objects.active().get().order, 1)
        retired = SegmentSelection.objects.get(pk=selections[1].pk)
        self.assertIsNotNone(retired.retired_at)
        self.assertEqual(retired.retired_by, self.user)
        dashboard = self.client.get(reverse("analysis:project_dashboard", args=[self.project.pk]))
        self.assertEqual(list(dashboard.context["selections"]), list(SegmentSelection.objects.active()))
        self.assertEqual(
            self.client.post(reverse("analysis:deselect_segment", args=[retired.pk])).status_code,
            404,
        )

    def test_sorting_does_not_change_persisted_selection_order(self):
        run = self.run_analysis()
        segments = list(run.segments.order_by("order"))
        for segment in segments:
            self.client.post(reverse("analysis:select_segment", args=[segment.pk]), {})
        before = list(self.project.segment_selections.values_list("pk", flat=True))
        self.assertEqual(
            self.client.get(
                reverse("analysis:run_detail", args=[run.pk]) + "?sort=score"
            ).status_code,
            200,
        )
        self.assertEqual(
            before,
            list(self.project.segment_selections.values_list("pk", flat=True)),
        )

    def test_exclusion_is_visible_and_can_be_restored_by_selecting(self):
        run = self.run_analysis()
        segment = run.segments.first()
        response = self.client.post(
            reverse("analysis:exclude_segment", args=[segment.pk]),
            {"reason": "Too broad for a standalone clip."},
        )
        self.assertRedirects(response, reverse("analysis:run_detail", args=[run.pk]))
        review = AnalysisSegmentReview.objects.get()
        self.assertEqual((review.decision, review.reason), ("excluded", "Too broad for a standalone clip."))
        detail = self.client.get(reverse("analysis:run_detail", args=[run.pk]))
        self.assertContains(detail, "Excluded")
        self.assertContains(detail, "Too broad for a standalone clip.")
        self.client.post(reverse("analysis:select_segment", args=[segment.pk]), {})
        self.assertTrue(SegmentSelection.objects.active().filter(analysis_segment=segment).exists())
        self.assertEqual(
            AnalysisSegmentReview.objects.order_by("-pk").first().decision,
            "selected",
        )

    def test_cross_user_objects_are_hidden_and_locked_project_rejects_run(self):
        run = self.run_analysis()
        segment = run.segments.first()
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("analysis:project_dashboard", args=[self.project.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("analysis:run_detail", args=[run.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("analysis:select_segment", args=[segment.pk]), {}
            ).status_code,
            404,
        )

        self.client.force_login(self.user)
        self.project.is_locked = True
        self.project.locked_at = self.project.approved_at
        self.project.save(update_fields=["is_locked", "locked_at"])
        response = self.client.post(
            reverse("analysis:run_analysis", args=[self.source.pk]),
            {"fallback_max_chunks": 2, "max_provider_attempts": 1},
        )
        self.assertRedirects(
            response,
            reverse("analysis:project_dashboard", args=[self.project.pk]),
        )
        self.assertEqual(AnalysisRun.objects.count(), 1)

    def test_csrf_is_required_for_analysis_mutations(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(
            reverse("analysis:run_analysis", args=[self.source.pk]),
            {"fallback_max_chunks": 2, "max_provider_attempts": 1},
        )
        self.assertEqual(response.status_code, 403)

    def test_project_page_links_to_analysis(self):
        response = self.client.get(reverse("project_detail", args=[self.project.pk]))
        self.assertContains(
            response,
            reverse("analysis:project_dashboard", args=[self.project.pk]),
        )

    @override_settings(OPENAI_API_KEY="test-secret", OPENAI_ANALYSIS_MODEL="test-model")
    @patch("analysis.services.jobs.OpenAITranscriptAnalysisProvider")
    def test_run_analysis_uses_topic_provider_when_configured(self, provider_class):
        provider = provider_class.return_value
        provider.name = "openai"
        provider.model = "test-model"
        provider.prompt_version = "topic-segmentation-v1"
        provider.analyze.return_value = {
            "segments": [{
                "start_sequence": 1,
                "end_sequence": 4,
                "title": "One conversation topic",
                "summary": "All transcript chunks discuss one topic.",
                "topic_labels": ["topic"],
                "scores": {
                    "relevance": 80, "clarity": 80, "factual_density": 80, "novelty": 80,
                    "controversy": 80, "reaction_potential": 80, "clip_suitability": 80,
                },
                "rationale": "The discussion stays on one topic.",
                "editorial_recommendation": {
                    "primary_approach": "critic",
                    "secondary_approaches": ["verify"],
                    "confidence": 82,
                    "reasoning": "The segment contains a clear claim to assess.",
                    "suggested_angle": "Test the claim against a practical counterexample.",
                    "research_needed": True,
                },
            }],
        }
        response = self.client.post(
            reverse("analysis:run_analysis", args=[self.source.pk]),
            {"fallback_max_chunks": 2, "max_provider_attempts": 1},
        )
        from analysis.services.jobs import AnalysisJobService
        from production.services.jobs import PipelineJobService
        from production.models import PipelineJob
        self.assertRedirects(response, reverse("production:project_jobs", args=[self.project.pk]))
        provider_class.assert_not_called()
        self.assertFalse(AnalysisRun.objects.exists())
        job = PipelineJobService.start(PipelineJob.objects.get(job_type="transcript_analysis"))
        run = AnalysisJobService.process_job(job)
        provider_class.assert_called_once_with("test-model")
        self.assertFalse(run.used_fallback)
        self.assertEqual(run.provider, "openai")
        self.assertEqual(run.model, "test-model")
        self.assertEqual(run.segments.get().title, "One conversation topic")
        detail = self.client.get(reverse("analysis:run_detail", args=[run.pk]))
        self.assertContains(detail, "Suggested approach: Critic")
        self.assertContains(detail, "Research recommended before use.")
