import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import SourceVideo, TranscriptChunk, VideoProject
from .services import DuplicateSourceError, TranscriptRemoteError


class CanonicalModelTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="model-owner")
        self.project = VideoProject.objects.create(owner=self.owner, title="Model project")

    def test_project_supports_multiple_sources_and_project_scoped_uniqueness(self):
        SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://youtu.be/dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="One",
        )
        SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://youtu.be/jwXITbC9pVI",
            youtube_video_id="jwXITbC9pVI",
            title="Two",
        )
        self.assertEqual(self.project.source_videos.count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceVideo.objects.create(
                project=self.project,
                youtube_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
                youtube_video_id="dQw4w9WgXcQ",
                title="Duplicate",
            )

        other = VideoProject.objects.create(owner=self.owner, title="Other")
        SourceVideo.objects.create(
            project=other,
            youtube_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="Allowed elsewhere",
        )

    def test_project_owner_is_protected(self):
        with self.assertRaises(ProtectedError):
            self.owner.delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
class CanonicalViewIntegrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="owner", password="password")
        self.other = User.objects.create_user(username="other", password="password")
        self.staff = User.objects.create_user(
            username="staff", password="password", is_staff=True
        )
        self.client.force_login(self.owner)

    @staticmethod
    def fetched_transcript():
        return [
            SimpleNamespace(start=0.0, duration=1.0, text="First chunk"),
            SimpleNamespace(start=1.0, duration=2.0, text="Second chunk"),
        ]

    def create_project(self, title="View project"):
        response = self.client.post(reverse("project_create"), {"title": title})
        project = VideoProject.objects.get(title=title)
        self.assertRedirects(response, reverse("project_detail", args=[project.pk]))
        self.assertEqual(project.owner, self.owner)
        return project

    def ingest(self, project, video_id, title):
        with (
            patch("scraper.services.YouTubeTranscriptApi.fetch", return_value=self.fetched_transcript()),
            patch("scraper.services.YouTubeMetadataClient.fetch_title", return_value=title),
        ):
            return self.client.post(
                reverse("project_ingest", args=[project.pk]),
                {"youtube_url": f"https://youtu.be/{video_id}"},
            )

    def test_project_crud_authorization_staff_access_and_post_enforcement(self):
        project = self.create_project()
        update = self.client.post(
            reverse("project_update", args=[project.pk]),
            {"title": "Updated", "description": "Description"},
        )
        self.assertRedirects(update, reverse("project_detail", args=[project.pk]))
        project.refresh_from_db()
        self.assertEqual(project.title, "Updated")

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("project_detail", args=[project.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("project_list")), "Updated")

        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("project_detail", args=[project.pk])), "Updated")
        self.assertEqual(self.client.get(reverse("project_approve", args=[project.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse("project_delete", args=[project.pk])).status_code, 405)

    def test_end_to_end_ingestion_duplicate_workflow_and_saved_pages(self):
        project = self.create_project("Pipeline")
        self.ingest(project, "dQw4w9WgXcQ", "First video")
        self.ingest(project, "jwXITbC9pVI", "Second video")
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.TRANSCRIPT_READY)
        self.assertEqual(project.source_videos.count(), 2)
        self.assertEqual(TranscriptChunk.objects.count(), 4)

        first_source = project.source_videos.get(youtube_video_id="dQw4w9WgXcQ")
        self.assertContains(self.client.get(reverse("source_video_list")), "First video")
        detail = self.client.get(reverse("transcript_detail", args=[first_source.pk]))
        self.assertContains(detail, "First chunk")
        self.assertContains(detail, "Second chunk")
        self.assertLess(detail.content.index(b"First chunk"), detail.content.index(b"Second chunk"))

        with patch("scraper.services.YouTubeTranscriptApi.fetch") as fetch:
            self.client.post(
                reverse("project_ingest", args=[project.pk]),
                {"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )
            fetch.assert_not_called()
        self.assertEqual(project.source_videos.count(), 2)

        self.client.post(reverse("project_approve", args=[project.pk]))
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.APPROVED)
        self.assertIsNotNone(project.approved_at)
        self.client.post(reverse("project_lock", args=[project.pk]))
        project.refresh_from_db()
        self.assertTrue(project.is_locked)

        self.client.post(
            reverse("project_update", args=[project.pk]),
            {"title": "Forbidden"},
        )
        project.refresh_from_db()
        self.assertEqual(project.title, "Pipeline")
        with patch("scraper.services.YouTubeTranscriptApi.fetch") as fetch:
            self.client.post(
                reverse("project_ingest", args=[project.pk]),
                {"youtube_url": "aqz-KE-bpKQ"},
            )
            fetch.assert_not_called()

        self.client.post(reverse("project_unlock", args=[project.pk]))
        project.refresh_from_db()
        self.assertFalse(project.is_locked)
        self.assertEqual(project.status, VideoProject.Status.APPROVED)
        self.client.post(reverse("project_archive", args=[project.pk]))
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.ARCHIVED)
        self.assertIsNotNone(project.approved_at)

    def test_source_and_project_deletion_require_confirmation_and_eligible_state(self):
        project = self.create_project("Delete source")
        self.ingest(project, "dQw4w9WgXcQ", "Delete me")
        source = project.source_videos.get()
        self.client.post(reverse("delete_transcript", args=[source.pk]))
        self.assertTrue(SourceVideo.objects.filter(pk=source.pk).exists())
        self.client.post(
            reverse("delete_transcript", args=[source.pk]),
            {"confirm": "yes"},
        )
        self.assertFalse(SourceVideo.objects.filter(pk=source.pk).exists())
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.DRAFT)

        cascade_project = self.create_project("Delete project")
        self.ingest(cascade_project, "jwXITbC9pVI", "Cascade")
        cascade_source = cascade_project.source_videos.get()
        self.client.post(
            reverse("project_delete", args=[cascade_project.pk]),
            {"confirm": "yes"},
        )
        self.assertFalse(VideoProject.objects.filter(pk=cascade_project.pk).exists())
        self.assertFalse(SourceVideo.objects.filter(pk=cascade_source.pk).exists())

        approved = self.create_project("Protected")
        approved.status = VideoProject.Status.APPROVED
        approved.approved_at = timezone.now()
        approved.save(update_fields=["status", "approved_at"])
        self.client.post(
            reverse("project_delete", args=[approved.pk]),
            {"confirm": "yes"},
        )
        self.assertTrue(VideoProject.objects.filter(pk=approved.pk).exists())

    def test_cross_user_source_access_and_api_error_sanitization(self):
        project = self.create_project("Private")
        self.ingest(project, "dQw4w9WgXcQ", "Private source")
        source = project.source_videos.get()
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse("transcript_detail", args=[source.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("fetch_transcript_api"),
                data=json.dumps({"project_id": project.pk, "url": "jwXITbC9pVI"}),
                content_type="application/json",
            ).status_code,
            404,
        )

        self.client.force_login(self.owner)
        with patch(
            "scraper.services.YouTubeTranscriptApi.fetch",
            side_effect=RuntimeError("secret remote detail"),
        ):
            response = self.client.post(
                reverse("fetch_transcript_api"),
                data=json.dumps({"project_id": project.pk, "url": "jwXITbC9pVI"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret remote detail", response.content.decode())

    def test_legacy_scraper_entry_redirects_to_canonical_projects(self):
        self.assertRedirects(self.client.get(reverse("scraper_form")), reverse("project_list"))


class FetchTranscriptCommandTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="command-owner")
        self.project = VideoProject.objects.create(owner=self.owner, title="Command project")

    @patch("scraper.management.commands.fetch_transcript.TranscriptService")
    def test_existing_project_calls_service(self, Service):
        Service.return_value.ingest.return_value = SimpleNamespace(
            chunks_created=2,
            source_video=SimpleNamespace(pk=9, title="Command title"),
        )
        stdout = StringIO()
        call_command(
            "fetch_transcript",
            "--url",
            "dQw4w9WgXcQ",
            "--project-id",
            str(self.project.pk),
            stdout=stdout,
        )
        Service.return_value.ingest.assert_called_once_with(self.project, "dQw4w9WgXcQ")
        self.assertIn("Saved 2 transcript chunks", stdout.getvalue())

    @patch("scraper.management.commands.fetch_transcript.TranscriptService")
    def test_duplicate_is_a_documented_non_error_outcome(self, Service):
        Service.return_value.ingest.side_effect = DuplicateSourceError(
            SimpleNamespace(pk=7)
        )
        stdout = StringIO()
        call_command(
            "fetch_transcript",
            "--url",
            "dQw4w9WgXcQ",
            "--project-id",
            str(self.project.pk),
            stdout=stdout,
        )
        self.assertIn("Duplicate", stdout.getvalue())

    @patch("scraper.management.commands.fetch_transcript.TranscriptService")
    def test_failed_new_project_is_removed_and_failure_is_nonzero(self, Service):
        Service.return_value.ingest.side_effect = TranscriptRemoteError("remote")
        with self.assertRaises(CommandError):
            call_command(
                "fetch_transcript",
                "--url",
                "dQw4w9WgXcQ",
                "--new-project-title",
                "Temporary",
                "--owner",
                self.owner.username,
            )
        self.assertFalse(VideoProject.objects.filter(title="Temporary").exists())

    def test_new_project_requires_explicit_active_owner(self):
        with self.assertRaises(CommandError):
            call_command(
                "fetch_transcript",
                "--url",
                "dQw4w9WgXcQ",
                "--new-project-title",
                "No owner",
            )
