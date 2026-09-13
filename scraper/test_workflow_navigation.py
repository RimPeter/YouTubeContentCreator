from django.contrib.auth import get_user_model
from django.test import TestCase
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
