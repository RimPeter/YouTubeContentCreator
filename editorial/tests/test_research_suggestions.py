from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import reverse

from editorial.services.access import EditorialServiceError
from editorial.services.research_suggestions import suggest_from_transcript
from .helpers import EditorialTestCase, create_approved_clip


class TranscriptSuggestionTests(SimpleTestCase):
    def test_topics_produce_relevant_directions_without_asserting_facts(self):
        cases = [
            ("The city has self-driving cars and drone deliveries.", "autonomous services"),
            ("Huaqiangbei has tech stores selling parts also found on Amazon.", "online marketplaces"),
            ("These smart glasses are the sponsor of this video.", "wearable features"),
            ("After visiting this city, the nature and architecture made the trip wonderful.", "residents' everyday experience"),
        ]
        for transcript, expected in cases:
            with self.subTest(transcript=transcript):
                options = suggest_from_transcript(transcript)
                self.assertIn(expected, options[0]["research_question"])
                self.assertEqual(len(options), 3)

    def test_unrecognized_topic_stays_grounded_and_within_form_limits(self):
        text = "The potter says that firing clay slowly reduces cracks in the finished bowl."
        for option in suggest_from_transcript(text):
            self.assertIn(text, option["research_question"])
        for option in suggest_from_transcript("long passage " * 1000):
            self.assertLessEqual(len(option["research_question"]), 500)
            self.assertLessEqual(len(option["editorial_focus"]), 4000)

    def test_missing_transcript_has_a_clear_error(self):
        with self.assertRaisesMessage(EditorialServiceError, "no transcript"):
            suggest_from_transcript(" [music] >> ")


class ResearchSuggestionViewTests(EditorialTestCase):
    def setUp(self):
        self.user, self.project, _, _, self.clip = create_approved_clip("suggestion-owner")
        self.client.force_login(self.user)
        self.url = reverse("editorial:research_suggestions", args=[self.clip.pk])

    def test_read_only_suggestions_can_be_edited_and_created(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Research suggestions")
        self.assertEqual(len(response.context["suggestions"]), 3)
        self.assertFalse(self.clip.research_packages.exists())
        response = self.client.post(reverse("editorial:create_research", args=[self.clip.pk]), {
            "research_question": "My edited question?", "editorial_focus": "My edited focus.",
        })
        package = self.clip.research_packages.get()
        self.assertRedirects(response, reverse("editorial:research_detail", args=[package.pk]))
        self.assertEqual(package.research_question, "My edited question?")
        self.assertEqual(package.editorial_focus, "My edited focus.")

    def test_other_owner_cannot_read_transcript_or_suggestions(self):
        other = get_user_model().objects.create_user("suggestion-other")
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_unapproved_and_stale_clips_cannot_offer_research(self):
        self.clip.status = "validated"
        self.clip.save(update_fields=["status"])
        self.assertContains(self.client.get(self.url, follow=True), "Approve this clip")
        self.clip.status = "approved"
        self.clip.save(update_fields=["status"])
        with patch("editorial.services.research_suggestions.SourceClipService.is_stale", return_value=True):
            self.assertContains(self.client.get(self.url, follow=True), "regenerate and approve")

    def test_transcript_html_is_escaped_in_editable_fields(self):
        payload = '</textarea><script>alert("test")</script>'
        with patch("editorial.views.suggestions_for_clip", return_value=suggest_from_transcript(payload)):
            response = self.client.get(self.url)
        self.assertNotContains(response, payload)
        self.assertContains(response, "&lt;script&gt;")
