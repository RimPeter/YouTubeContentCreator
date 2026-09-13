from django.test import TestCase
from django.urls import reverse

from analysis.services import AnalysisService, SelectionService
from analysis.tests.helpers import FakeProvider, create_approved_source, valid_provider_output
from editorial.models import ReactionTimelineItem
from editorial.services.reaction_sequence_drafts import ReactionSequenceDraftService
from editorial.services.reaction_sequence_plans import ReactionSequencePlanService
from editorial.services.reaction_timelines import ReactionTimelineService
from editorial.services.reaction_production import ReactionProductionService


class ReactionTimelineTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, _chunks = create_approved_source("reaction-timeline-owner")
        run = AnalysisService(FakeProvider([valid_provider_output()])).analyze(self.source, self.user)
        self.selections = [
            SelectionService.select(self.project, segment, self.user)[0]
            for segment in run.segments.order_by("order")
        ]
        plan = ReactionSequencePlanService.create(self.project, self.user)
        ReactionSequencePlanService.mark_ready(plan, self.user, research_reviewed=True)
        self.draft = ReactionSequenceDraftService.generate(plan, self.user, use_ai=False)

    def test_creates_interleaved_source_and_creator_transcripts(self):
        timeline = ReactionTimelineService.create(self.draft, self.user)
        items = list(timeline.items.order_by("order"))

        self.assertEqual(
            [item.item_type for item in items],
            ["creator", "source", "creator", "source", "creator", "creator"],
        )
        source_items = [item for item in items if item.item_type == ReactionTimelineItem.ItemType.SOURCE]
        self.assertEqual(
            [item.transcript_text for item in source_items],
            list(self.draft.sections.values_list("plan_section__transcript_snapshot", flat=True)),
        )
        self.assertEqual(source_items[0].source_start_seconds, self.selections[0].analysis_segment.start_seconds)
        self.assertEqual(source_items[0].source_end_seconds, self.selections[0].analysis_segment.end_seconds)
        self.assertFalse(ReactionTimelineService.is_stale(timeline))

    def test_views_filter_source_and_creator_turns(self):
        timeline = ReactionTimelineService.create(self.draft, self.user)
        self.client.force_login(self.user)

        source_response = self.client.get(reverse("editorial:reaction_timeline_detail", args=[timeline.pk]), {"view": "source"})
        creator_response = self.client.get(reverse("editorial:reaction_timeline_detail", args=[timeline.pk]), {"view": "creator"})

        self.assertEqual(source_response.status_code, 200)
        self.assertEqual(creator_response.status_code, 200)
        self.assertEqual(list(source_response.context["items"].values_list("item_type", flat=True)), ["source", "source"])
        self.assertEqual(list(creator_response.context["items"].values_list("item_type", flat=True)), ["creator"] * 4)

    def test_marks_timeline_stale_when_draft_text_changes(self):
        timeline = ReactionTimelineService.create(self.draft, self.user)
        section = self.draft.sections.first()
        section.reaction_text = "A revised reaction."
        section.save(update_fields=["reaction_text"])

        self.assertTrue(ReactionTimelineService.is_stale(timeline))

    def test_edits_creator_turn_trims_source_and_reorders_items(self):
        timeline = ReactionTimelineService.create(self.draft, self.user)
        source = timeline.items.get(item_type="source", order=2)
        creator = timeline.items.get(item_type="creator", order=3)

        ReactionTimelineService.update_creator_item(
            creator, self.user, label="My reflection", transcript_text="My revised reaction.", included=False,
        )
        ReactionTimelineService.update_source_item(
            source, self.user,
            source_start_seconds=source.source_start_seconds + 0.1,
            source_end_seconds=source.source_end_seconds - 0.1,
            included=False,
        )
        ReactionTimelineService.move_item(creator, self.user, "up")
        creator.refresh_from_db()
        source.refresh_from_db()

        self.assertEqual(creator.order, 2)
        self.assertEqual(creator.transcript_text, "My revised reaction.")
        self.assertFalse(creator.included)
        self.assertFalse(source.included)
        self.assertEqual(source.source_start_seconds, self.selections[0].analysis_segment.start_seconds + 0.1)

    def test_source_trim_cannot_escape_selected_segment(self):
        timeline = ReactionTimelineService.create(self.draft, self.user)
        source = timeline.items.get(item_type="source", order=2)

        with self.assertRaisesMessage(Exception, "inside the selected segment"):
            ReactionTimelineService.update_source_item(
                source, self.user,
                source_start_seconds=source.source_start_seconds - 0.1,
                source_end_seconds=source.source_end_seconds,
                included=True,
            )

    def test_adds_a_creator_add_on_for_reordering(self):
        timeline = ReactionTimelineService.create(self.draft, self.user)
        added = ReactionTimelineService.add_creator_item(
            timeline, self.user, label="Creator add-on", transcript_text="One more point for the audience.",
        )

        self.assertEqual(added.order, 7)
        self.assertEqual(added.item_type, ReactionTimelineItem.ItemType.CREATOR)
        self.assertEqual(added.transcript_text, "One more point for the audience.")

    def test_production_handoff_explains_missing_recordings_and_clips(self):
        timeline = ReactionTimelineService.create(self.draft, self.user)
        issues, _resolved = ReactionProductionService.readiness(timeline)

        self.assertTrue(any("approved current narration" in issue for issue in issues))
        self.assertTrue(any("approved source clip" in issue for issue in issues))
        with self.assertRaisesMessage(Exception, "approved current narration"):
            ReactionProductionService.create_assembly(timeline, self.user)
