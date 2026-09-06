import os
import runpy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from editorial.models import ResearchPackage
from production.models import PipelineJob, SourceClip
from production.tests.helpers import create_selected_segment, create_validated_asset
from scraper.models import VideoProject


class DashboardTests(TestCase):
    def setUp(self):
        # Canonical history migrations may create a bootstrap project.
        self.initial_active_count = VideoProject.objects.exclude(status=VideoProject.Status.ARCHIVED).count()
        self.owner = get_user_model().objects.create_user(username="dashboard-owner")
        self.other = get_user_model().objects.create_user(username="dashboard-other")
        self.project = VideoProject.objects.create(
            owner=self.owner, title="Owner review project", status=VideoProject.Status.TRANSCRIPT_READY
        )
        self.other_project = VideoProject.objects.create(owner=self.other, title="Other private project")
        self.archived = VideoProject.objects.create(
            owner=self.owner, title="Old archived work", status=VideoProject.Status.ARCHIVED,
            archived_at=timezone.now(),
        )
        self.job(self.project, "failed")
        self.job(self.other_project, "failed")
        self.job(self.archived, "failed")

    def job(self, project, status):
        return PipelineJob.objects.create(
            project=project, job_type="clip_trim", status=status,
            idempotency_key="a" * 64, input_fingerprint="b" * 64,
            requested_by=project.owner,
        )

    def test_anonymous_dashboard_does_not_expose_projects_or_counts(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in")
        self.assertNotContains(response, self.project.title)
        self.assertNotContains(response, self.other_project.title)
        self.assertNotIn("summary", response.context)

    def test_owner_counts_exclude_other_owners_and_archived_work(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["summary"], {
            "active_projects": 1, "pending_reviews": 1, "failed_jobs": 1, "processing_jobs": 0,
        })
        self.assertEqual(response.context["archived_count"], 1)
        self.assertContains(response, self.project.title)
        self.assertNotContains(response, self.other_project.title)
        self.assertNotContains(response, self.archived.title)
        self.assertContains(response, "Review transcripts")
        self.assertContains(response, reverse("production:project_jobs", args=[self.project.pk]))

    def test_staff_can_see_all_owners_but_counts_still_exclude_archived(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["summary"]["active_projects"], self.initial_active_count + 2)
        self.assertEqual(response.context["summary"]["failed_jobs"], 2)
        self.assertContains(response, self.other_project.title)
        self.assertContains(response, "Staff view")

    def test_history_and_retired_selections_do_not_inflate_reviews(self):
        owner, project, source, selection = create_selected_segment("dashboard-selected")
        asset = create_validated_asset(project, owner)
        old_clip = SourceClip.objects.create(
            project=project, selected_segment=selection, source_asset=asset,
            pipeline_job=self.job(project, "succeeded"), version=1, status="stale",
            input_fingerprint="c" * 64, requested_start_seconds=1, requested_end_seconds=3,
            expected_duration_seconds=2,
        )
        SourceClip.objects.create(
            project=project, selected_segment=selection, source_asset=asset,
            pipeline_job=self.job(project, "succeeded"), version=2, status="validated",
            input_fingerprint="d" * 64, requested_start_seconds=1, requested_end_seconds=3,
            expected_duration_seconds=2,
        )
        ResearchPackage.objects.create(
            project=project, source_clip=old_clip, version=1, status="draft",
            input_fingerprint="e" * 64, research_question="Old research?", editorial_focus="Old draft",
        )
        self.client.force_login(owner)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["summary"]["pending_reviews"], 1)
        row = response.context["project_rows"][0]
        self.assertEqual(row["stale"], 0)
        self.assertEqual(row["next_label"], "Review clips")
        selection.retired_at = timezone.now()
        selection.save(update_fields=["retired_at"])
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["summary"]["pending_reviews"], 0)
        self.assertEqual(response.context["project_rows"][0]["selection_count"], 0)
        self.assertContains(response, "Analyze and select segments")

    def test_account_pages_share_navigation_and_login_preserves_next(self):
        response = self.client.get(reverse("account_login"), {"next": reverse("project_list")})
        self.assertContains(response, 'aria-label="Main navigation"')
        self.assertContains(response, 'name="next" value="/projects/"')
        response = self.client.get(reverse("account_signup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Main navigation"')

    def test_next_action_requires_eligible_source_video(self):
        owner, project, source, selection = create_selected_segment("dashboard-media")
        asset = create_validated_asset(project, owner)
        self.client.force_login(owner)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["project_rows"][0]["next_label"], "Upload source media")
        asset.duration_seconds = 10
        asset.rights_basis = "user_owned"
        asset.consent_metadata = {"rights_confirmed": True}
        asset.save(update_fields=["duration_seconds", "rights_basis", "consent_metadata"])
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["project_rows"][0]["next_label"], "Create clips")


class EnvironmentConfigurationTests(SimpleTestCase):
    def load_settings(self, environment):
        with patch.dict(os.environ, environment, clear=True):
            return runpy.run_path(str(settings.BASE_DIR / "YoutubeContent" / "settings.py"))

    def test_production_never_uses_development_key_or_debug(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "DJANGO_SECRET_KEY"):
            self.load_settings({"DJANGO_ENV": "production"})
        with self.assertRaisesMessage(ImproperlyConfigured, "DJANGO_DEBUG must be false"):
            self.load_settings({"DJANGO_ENV": "production", "DJANGO_DEBUG": "true"})

    def test_production_configuration_uses_smtp_and_isolated_paths(self):
        config = self.load_settings({
            "DJANGO_ENV": "production", "DJANGO_SECRET_KEY": "test-" + "aB3q9x" * 12,
            "DJANGO_ALLOWED_HOSTS": "creator.example.com", "DJANGO_SMTP_HOST": "smtp.example.com",
            "DJANGO_DB_PATH": "isolated.sqlite3", "DJANGO_MEDIA_ROOT": "isolated-media",
            "DJANGO_STATIC_ROOT": "isolated-static",
        })
        self.assertFalse(config["DEBUG"])
        self.assertTrue(config["SESSION_COOKIE_SECURE"])
        self.assertTrue(config["CSRF_COOKIE_SECURE"])
        self.assertEqual(config["DATABASES"]["default"]["NAME"], "isolated.sqlite3")
        self.assertEqual(config["MEDIA_ROOT"], Path("isolated-media"))
        self.assertEqual(config["MAILERS"]["default"]["OPTIONS"]["host"], "smtp.example.com")
        self.assertNotIn("SECURE_PROXY_SSL_HEADER", config)

    def test_local_key_initialization_preserves_existing_key(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".local-secret-key"
            with override_settings(PRODUCTION=False, LOCAL_SECRET_KEY_FILE=path):
                call_command("init_local_settings", stdout=StringIO())
                key = path.read_text(encoding="utf-8")
                self.assertGreaterEqual(len(key.strip()), 50)
                call_command("init_local_settings", stdout=StringIO())
                self.assertEqual(path.read_text(encoding="utf-8"), key)
