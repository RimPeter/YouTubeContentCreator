from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape
from analysis.tests.helpers import create_approved_source
from scraper.workflow import STAGES


class WorkflowNavigationTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, _ = create_approved_source("sidebar-owner")
        self.client.force_login(self.user)

    def test_all_stage_landings_and_empty_guidance(self):
        for key, label, _, _ in STAGES:
            response = self.client.get(reverse("project_workflow", args=[self.project.pk, key]), follow=True)
            self.assertEqual(response.status_code, 200, key)
            self.assertContains(response, 'aria-label="Workflow stages"')
            self.assertContains(response, escape(label))
        response = self.client.get(reverse("project_workflow", args=[self.project.pk, "timeline"]))
        self.assertContains(response, "No saved work in this stage yet")
        self.assertContains(response, 'aria-current="page">Reaction timeline')

    def test_non_project_pages_do_not_show_sidebar(self):
        self.assertNotContains(self.client.get(reverse("project_list")), 'aria-label="Workflow stages"')

    def test_source_context_ownership_and_staff(self):
        response = self.client.get(reverse("transcript_detail", args=[self.source.pk]))
        self.assertContains(response, 'aria-current="page">Sources &amp; transcripts')
        self.client.force_login(get_user_model().objects.create_user("sidebar-other"))
        self.assertEqual(self.client.get(reverse("project_workflow", args=[self.project.pk, "recording"])).status_code, 404)
        staff = get_user_model().objects.create_user("sidebar-staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("project_workflow", args=[self.project.pk, "recording"])).status_code, 200)

    def test_locked_archived_and_invalid_stages(self):
        self.project.is_locked = True
        self.project.save(update_fields=["is_locked"])
        url = reverse("project_workflow", args=[self.project.pk, "timeline"])
        self.assertContains(self.client.get(url), "read-only")
        self.project.status = "archived"
        self.project.save(update_fields=["status"])
        self.assertContains(self.client.get(url), "read-only")
        self.assertEqual(self.client.get(reverse("project_workflow", args=[self.project.pk, "unknown"])).status_code, 404)

    def test_current_versions_determine_stage_status_and_dashboard_action(self):
        from analysis.services import AnalysisService, SelectionService
        from editorial.services.reaction_sequence_plans import ReactionSequencePlanService
        from editorial.services.reaction_sequence_drafts import ReactionSequenceDraftService
        from editorial.services.reaction_timelines import ReactionTimelineService
        from editorial.models import ReactionProductionAssembly
        from scraper.workflow import navigation
        run = AnalysisService().analyze(self.source, self.user)
        SelectionService.select(self.project, run.segments.first(), self.user)
        old_plan = ReactionSequencePlanService.create(self.project, self.user)
        old_plan.status = "stale"
        old_plan.save(update_fields=["status"])
        plan = ReactionSequencePlanService.create(self.project, self.user)
        plan = ReactionSequencePlanService.mark_ready(plan, self.user, research_reviewed=True)
        draft = ReactionSequenceDraftService.generate(plan, self.user, use_ai=False)
        timeline = ReactionTimelineService.create(draft, self.user)
        timeline.status = "ready"
        timeline.save(update_fields=["status"])
        ReactionProductionAssembly.objects.create(timeline=timeline, version=1, status="stale", input_fingerprint="a" * 64)
        ReactionProductionAssembly.objects.create(timeline=timeline, version=2, status="ready", input_fingerprint="b" * 64)
        rows = {row["key"]: row for row in navigation(self.project)}
        for stage in ("analysis", "segments", "plan", "script", "timeline", "recording", "handoff"):
            self.assertEqual(rows[stage]["status"], "Complete", stage)
        self.assertEqual(rows["plan"]["count"], 1)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "View production handoff")

    def test_superseded_job_failure_does_not_block_jobs_stage(self):
        from production.models import PipelineJob
        from scraper.workflow import navigation
        for status in ("failed", "succeeded"):
            PipelineJob.objects.create(project=self.project, job_type="sequence_reaction", status=status,
                idempotency_key="a" * 64, input_fingerprint="b" * 64, requested_by=self.user)
        rows = {row["key"]: row for row in navigation(self.project)}
        self.assertEqual(rows["jobs"]["status"], "Complete")

    @override_settings(STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    })
    def test_ready_research_and_approved_clips_are_complete(self):
        from editorial.tests.helpers import create_ready_package
        from scraper.workflow import navigation
        _, project, _, _, _, _ = create_ready_package("navigation-research")
        rows = {row["key"]: row for row in navigation(project)}
        self.assertEqual(rows["clips"]["status"], "Complete")
        self.assertEqual(rows["research"]["status"], "Complete")
