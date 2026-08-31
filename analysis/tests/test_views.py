from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from analysis.models import AnalysisRun, SegmentSelection
from scraper.models import VideoProject

from .helpers import create_approved_source


@override_settings(ALLOWED_HOSTS=["testserver"])
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
        self.assertEqual(SegmentSelection.objects.count(), 1)
        self.assertEqual(SegmentSelection.objects.get().order, 1)

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
