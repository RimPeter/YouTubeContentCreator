from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.html import escape

from editorial.services.research import ResearchService
from editorial.services.reactions import ReactionService

from .helpers import EditorialTestCase, create_approved_clip


class EditorialViewTests(EditorialTestCase):
    def setUp(self):
        self.user, self.project, _, _, self.clip = create_approved_clip("view-owner")
        self.other = get_user_model().objects.create_user("view-other", password="password")
        self.client.force_login(self.user)

    def test_create_url_get_opens_form_without_creating_a_package(self):
        response = self.client.get(reverse("editorial:project_editorial", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("editorial:create_research", args=[self.clip.pk]))
        target = reverse("editorial:project_editorial", args=[self.project.pk])
        self.assertRedirects(response, f"{target}#research-clip-{self.clip.pk}")
        self.assertFalse(self.clip.research_packages.exists())
        response = self.client.get(response.url)
        self.assertContains(response, f'id="research-clip-{self.clip.pk}"')
        self.assertContains(response, "Suggest research directions")

    def test_create_url_get_respects_ownership(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("editorial:create_research", args=[self.clip.pk]))
        self.assertEqual(response.status_code, 404)

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

    def test_generated_reaction_redirect_renders_review_and_approval_page(self):
        package = ResearchService.create_package(
            self.clip, self.user, research_question="Question", editorial_focus="Focus"
        )
        ResearchService.mark_ready(package, self.user)
        response = self.client.post(
            reverse("editorial:generate_reaction", args=[package.pk]), follow=True,
        )
        reaction = package.reaction_blocks.get()
        self.assertContains(response, escape(reaction.combined_script))
        self.assertContains(response, reverse("editorial:research_detail", args=[package.pk]))
        self.assertContains(response, "Human approval")
        response = self.client.post(reverse("editorial:approve_reaction", args=[reaction.pk]), {
            "evidence_reviewed": "on", "originality_confirmed": "on",
        }, follow=True)
        self.assertContains(response, "Approved")
        self.assertNotContains(response, "Save and rebuild combined script")
