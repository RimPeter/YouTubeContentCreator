from django.contrib.auth import get_user_model
from django.test import TestCase

from analysis.services import AnalysisService, SelectionService
from analysis.tests.helpers import FakeProvider, create_approved_source, valid_provider_output
from editorial.models import ReactionSequencePlan
from editorial.services.access import EditorialServiceError
from editorial.services.reaction_sequence_drafts import ReactionSequenceDraftService
from editorial.services.reaction_sequence_plans import ReactionSequencePlanService


class ReactionSequencePlanServiceTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, _chunks = create_approved_source("reaction-plan-owner")
        self.run = AnalysisService(FakeProvider([valid_provider_output()])).analyze(self.source, self.user)
        self.selections = [
            SelectionService.select(self.project, segment, self.user)[0]
            for segment in self.run.segments.order_by("order")
        ]

    def test_creates_an_ordered_plan_with_roles_bridges_and_research_flags(self):
        plan = ReactionSequencePlanService.create(self.project, self.user)
        sections = list(plan.sections.order_by("order"))
        self.assertEqual([section.selection_id for section in sections], [item.pk for item in self.selections])
        self.assertEqual(sections[0].role, "thesis")
        self.assertEqual(sections[1].role, "conclusion")
        self.assertTrue(sections[1].research_required)
        self.assertTrue(sections[1].bridge)
        self.assertEqual(plan.input_fingerprint, ReactionSequencePlanService.current_fingerprint(self.project))

    def test_edits_are_allowed_only_while_inputs_are_current(self):
        plan = ReactionSequencePlanService.create(self.project, self.user)
        ReactionSequencePlanService.update_plan(plan, self.user, {
            "overall_thesis": "A better connected thesis.",
            "audience_angle": "Give viewers a practical way to assess the claims.",
            "planned_conclusion": "End with one clear takeaway.",
        })
        section = plan.sections.get(order=2)
        ReactionSequencePlanService.update_section(section, self.user, {
            "role": "tension", "bridge": "Now test the claim.",
            "research_required": True, "research_reason": "The claim needs evidence.",
        })
        self.selections[0].notes = "Changed after planning"
        self.selections[0].save(update_fields=["notes", "updated_at"])
        with self.assertRaises(EditorialServiceError):
            ReactionSequencePlanService.update_plan(plan, self.user, {
                "overall_thesis": "No longer current.", "audience_angle": "Angle", "planned_conclusion": "Conclusion",
            })
        plan.refresh_from_db()
        self.assertEqual(plan.status, ReactionSequencePlan.Status.STALE)

    def test_requires_current_selections_and_ownership(self):
        other = get_user_model().objects.create_user("reaction-plan-other")
        with self.assertRaises(EditorialServiceError):
            ReactionSequencePlanService.create(self.project, other)
        for selection in self.selections:
            SelectionService.deselect(self.project, selection, self.user)
        with self.assertRaises(EditorialServiceError):
            ReactionSequencePlanService.create(self.project, self.user)

    def test_ready_plan_generates_a_traceable_continuous_fallback_draft(self):
        plan = ReactionSequencePlanService.create(self.project, self.user)
        with self.assertRaises(EditorialServiceError):
            ReactionSequencePlanService.mark_ready(plan, self.user, research_reviewed=False)
        ReactionSequencePlanService.mark_ready(plan, self.user, research_reviewed=True)
        draft = ReactionSequenceDraftService.generate(plan, self.user, use_ai=False)
        self.assertTrue(draft.used_fallback)
        self.assertEqual(draft.sections.count(), len(self.selections))
        self.assertTrue(draft.combined_script.startswith(draft.opening))
        self.assertEqual(
            list(draft.sections.values_list("plan_section__selection_id", flat=True)),
            [item.pk for item in self.selections],
        )
