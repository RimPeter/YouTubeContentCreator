from django.urls import reverse
from django.contrib.auth import get_user_model
from editorial.tests.helpers import EditorialTestCase, create_ready_package
from editorial.tests import test_recording_handoff
from django.utils import timezone
from editorial.services import crud
from editorial.services.access import EditorialServiceError
from editorial.models import TimelineNarrationTake
from scraper.models import WorkflowAudit
from scraper.ownership import transfer


class TimelineCorrectionTests(EditorialTestCase):
    setUp = test_recording_handoff.RecordingHandoffTests.setUp
    post = test_recording_handoff.RecordingHandoffTests.post
    record_all = test_recording_handoff.RecordingHandoffTests.record_all

    def test_long_text_and_stale_form_recovery(self):
        item = self.timeline.items.filter(item_type="creator").first()
        url = reverse("editorial:edit_timeline_creator_item", args=[item.pk])
        data = {"revision": item.revision, "label": "Long draft", "transcript_text": "a" * 10002, "included": "on"}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        data["transcript_text"] = "Old tab edits"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Old tab edits", status_code=409)
        item.refresh_from_db()
        self.assertEqual(len(item.transcript_text), 10002)

    def test_mutation_resets_status_and_withdrawal_preserves_history(self):
        self.record_all()
        self.post("create_reaction_assembly", self.timeline.pk)
        assembly = self.timeline.production_assemblies.get()
        take = TimelineNarrationTake.objects.filter(timeline_item__timeline=self.timeline, selected=True).first()
        self.client.post(reverse("editorial:withdraw_take", args=[take.pk]), {"action": "revoke"})
        take.refresh_from_db()
        self.assertFalse(take.selected)
        self.assertIsNone(take.approved_at)
        assembly.refresh_from_db()
        self.assertEqual(assembly.status, "stale")
        self.assertTrue(WorkflowAudit.objects.filter(action="narration_revoked").exists())
        item = take.timeline_item
        self.post("edit_timeline_creator_item", item.pk, {"label": "New", "transcript_text": "New text", "included": "on"})
        self.timeline.refresh_from_db()
        self.assertEqual(self.timeline.status, "draft")

    def test_ready_plan_revision_and_staff_transfer(self):
        plan = self.timeline.reaction_draft.plan
        new = crud.revise_plan(plan, self.user)
        self.assertEqual(new.status, "draft")
        self.assertIsNone(new.reviewed_at)
        self.assertEqual(new.overall_thesis, plan.overall_thesis)
        staff = get_user_model().objects.create_user("transfer-staff", is_staff=True)
        owner = get_user_model().objects.create_user("transfer-new")
        self.source.legacy_scraped_video_id = 1234
        self.source.save(update_fields=["legacy_scraped_video_id"])
        with self.assertRaises(ValueError):
            transfer(self.project, owner, self.user, "Unauthorized")
        transfer(self.project, owner, staff, "Assign imported history")
        self.assertEqual(self.client.get(reverse("project_detail", args=[self.project.pk])).status_code, 404)
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("project_detail", args=[self.project.pk])).status_code, 200)


class ResearchCorrectionTests(EditorialTestCase):
    def test_research_revision_edit_and_immutable_original(self):
        user, project, source, selection, clip, package = create_ready_package("research-correction")
        new = crud.revise_research(package, user)
        crud.correct_research(new, user, {"research_question": "Revised question", "editorial_focus": "Revised focus"})
        package.refresh_from_db()
        self.assertNotEqual(package.research_question, "Revised question")
        with self.assertRaises(EditorialServiceError):
            crud.correct_research(package, user, {"research_question": "Not allowed", "editorial_focus": "No"})
        from editorial.services.research import ResearchService
        evidence = ResearchService.add_evidence(new, user, source_url="https://example.com/evidence",
            title="Evidence", publisher="Publisher", classification="context", finding="Finding", relevance="Context", retrieved_on=timezone.now().date())
        ResearchService.set_verification(evidence, user, verified=True)
        crud.correct_evidence(evidence, user, {"source_url": evidence.source_url, "title": "Corrected title"})
        evidence.refresh_from_db()
        self.assertEqual(evidence.verification_status, "unverified")
        self.assertIsNone(evidence.verified_at)
