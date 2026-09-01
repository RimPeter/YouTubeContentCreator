from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from editorial.services.research import ResearchService
from editorial.services.reactions import ReactionService

from .helpers import create_approved_clip


class EditorialViewTests(TestCase):
    def setUp(self):
        self.user, self.project, _, _, self.clip = create_approved_clip("view-owner")
        self.other = get_user_model().objects.create_user("view-other", password="password")
        self.client.force_login(self.user)

    def test_dashboard_and_post_only_mutation(self):
        response = self.client.get(reverse("editorial:project_editorial", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("editorial:create_research", args=[self.clip.pk]))
        self.assertEqual(response.status_code, 405)

    def test_create_and_cross_user_visibility(self):
        response = self.client.post(reverse("editorial:create_research", args=[self.clip.pk]), {
            "research_question": "What does this mean?", "editorial_focus": "Analyze it."
        })
        self.assertEqual(response.status_code, 302)
        package = self.clip.research_packages.get()
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse("editorial:research_detail", args=[package.pk])).status_code, 404
        )

    def test_csrf_is_enforced_for_mutation(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(reverse("editorial:create_research", args=[self.clip.pk]), {
            "research_question": "Question", "editorial_focus": "Focus"
        })
        self.assertEqual(response.status_code, 403)

    def test_reaction_subsections_can_be_edited_and_recombined(self):
        package = ResearchService.create_package(
            self.clip, self.user, research_question="Question", editorial_focus="Focus"
        )
        ResearchService.mark_ready(package, self.user)
        block = ReactionService().generate(package, self.user)
        response = self.client.post(reverse("editorial:edit_reaction", args=[block.pk]), {
            "reframe": "Edited reframe", "focus": block.focus,
            "reaction_type": block.reaction_type, "evaluation": block.evaluation,
            "mini_essay_thesis": block.mini_essay_thesis,
            "mini_essay_script": block.mini_essay_script, "conclusion": block.conclusion,
            "bridge": block.bridge, "rationale": block.rationale,
        })
        self.assertEqual(response.status_code, 302)
        block.refresh_from_db()
        self.assertEqual(block.reframe, "Edited reframe")
        self.assertTrue(block.combined_script.startswith("Edited reframe"))
