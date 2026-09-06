import json
from queue import Queue
from threading import Event, Thread
from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from scraper.admin import SourceVideoInline, TranscriptChunkInline
from scraper.forms import VideoProjectForm
from scraper.models import SourceVideo, TranscriptChunk, VideoProject
from scraper.services import (
    ProjectMutationForbiddenError,
    ProjectWorkflowError,
    ProjectWorkflowService,
    TranscriptService,
    lock_project,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class ProjectLifecycleRegressionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="lifecycle-owner")
        self.project = VideoProject.objects.create(owner=self.owner, title="Original")
        self.fetch = Mock()
        self.fetch.fetch.return_value = [{"start": 0, "duration": 1, "text": "Transcript"}]
        self.metadata = Mock()
        self.metadata.fetch_title.return_value = "Source"
        self.service = TranscriptService(self.fetch, self.metadata)
        self.source = self.service.ingest(self.project, "dQw4w9WgXcQ").source_video
        self.project.refresh_from_db()

    def test_ingestion_rechecks_approval_archive_and_owner_after_remote_work(self):
        other = get_user_model().objects.create_user(username="new-owner")
        cases = (
            ("approval", lambda: ProjectWorkflowService.approve(self.project)),
            ("archive", lambda: ProjectWorkflowService.archive(self.project)),
            ("owner", lambda: VideoProject.objects.filter(pk=self.project.pk).update(owner=other)),
        )
        for name, mutation in cases:
            with self.subTest(mutation=name):
                VideoProject.objects.filter(pk=self.project.pk).update(
                    status=VideoProject.Status.TRANSCRIPT_READY,
                    approved_at=None,
                    archived_at=None,
                    owner=self.owner,
                )
                self.metadata.fetch_title.side_effect = lambda _url: (mutation(), "New source")[1]
                with self.assertRaises(ProjectMutationForbiddenError):
                    self.service.ingest(self.project, "jwXITbC9pVI", user=self.owner)
                self.assertEqual(self.project.source_videos.count(), 1)
                self.project.refresh_from_db()
                if name == "approval":
                    self.assertEqual(self.project.status, VideoProject.Status.APPROVED)
                elif name == "archive":
                    self.assertEqual(self.project.status, VideoProject.Status.ARCHIVED)
                else:
                    self.assertEqual(self.project.owner, other)

    def test_edit_submitted_before_approval_cannot_overwrite_it(self):
        self.client.force_login(self.owner)
        original_is_valid = VideoProjectForm.is_valid

        def approve_after_validation(form):
            valid = original_is_valid(form)
            ProjectWorkflowService.approve(self.project)
            return valid

        with patch.object(VideoProjectForm, "is_valid", approve_after_validation):
            response = self.client.post(reverse("project_update", args=[self.project.pk]), {"title": "Late edit"})
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.title, "Original")
        self.assertEqual(self.project.status, VideoProject.Status.APPROVED)
        self.assertIsNotNone(self.project.approved_at)

    def test_stale_project_and_source_delete_recheck_protection(self):
        ProjectWorkflowService.approve(self.project)
        with self.assertRaises(ProjectWorkflowError):
            ProjectWorkflowService.delete(self.project, self.owner)
        with self.assertRaises(ProjectWorkflowError):
            ProjectWorkflowService.delete_source(self.source, self.owner)
        self.assertTrue(SourceVideo.objects.filter(pk=self.source.pk).exists())
        self.assertTrue(VideoProject.objects.filter(pk=self.project.pk).exists())

    def test_edit_rechecks_owner_and_only_accepts_detail_fields(self):
        other = get_user_model().objects.create_user(username="replacement-owner")
        VideoProject.objects.filter(pk=self.project.pk).update(owner=other)
        with self.assertRaises(ProjectWorkflowError):
            ProjectWorkflowService.update(self.project, {"title": "Forbidden"}, self.owner)
        with self.assertRaises(ProjectWorkflowError):
            ProjectWorkflowService.update(self.project, {"status": "draft"}, other)
        updated = ProjectWorkflowService.update(self.project, {"title": "Allowed"}, other)
        self.assertEqual(updated.title, "Allowed")
        self.assertEqual(updated.status, VideoProject.Status.TRANSCRIPT_READY)
        self.assertEqual(updated.owner, other)

    def test_deleted_project_during_ingestion_returns_workflow_error(self):
        self.metadata.fetch_title.side_effect = lambda _url: (ProjectWorkflowService.delete(self.project), "Late")[1]
        with self.assertRaises(ProjectMutationForbiddenError):
            self.service.ingest(self.project, "jwXITbC9pVI", user=self.owner)
        self.assertFalse(SourceVideo.objects.exists())

    def test_failed_command_cleanup_preserves_a_project_another_request_used(self):
        self.assertFalse(ProjectWorkflowService.discard_empty_draft(self.project))
        self.assertTrue(SourceVideo.objects.filter(pk=self.source.pk).exists())
        unused = VideoProject.objects.create(owner=self.owner, title="Unused draft")
        self.assertTrue(ProjectWorkflowService.discard_empty_draft(unused))
        self.assertFalse(VideoProject.objects.filter(pk=unused.pk).exists())

    def test_invalid_api_shapes_and_field_types_return_400_without_fetching(self):
        self.client.force_login(self.owner)
        invalid = [
            [], None, "text", 42, {},
            {"project_id": "abc", "url": "jwXITbC9pVI"},
            {"project_id": True, "url": "jwXITbC9pVI"},
            {"project_id": 0, "url": "jwXITbC9pVI"},
            {"project_id": 2**100, "url": "jwXITbC9pVI"},
            {"project_id": self.project.pk, "url": 123},
            {"project_id": self.project.pk, "url": None},
            {"project_id": self.project.pk, "url": []},
        ]
        with patch("scraper.services.YouTubeTranscriptApi.fetch") as fetch:
            for payload in invalid:
                with self.subTest(payload=payload):
                    response = self.client.post(reverse("fetch_transcript_api"), json.dumps(payload), content_type="application/json")
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.json())
            fetch.assert_not_called()


@override_settings(ALLOWED_HOSTS=["testserver"])
class WorkflowAdminProtectionTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser("artifact-admin", "admin@example.com", "password")
        self.project = VideoProject.objects.create(
            owner=self.admin_user,
            title="Protected",
            status=VideoProject.Status.APPROVED,
            approved_at=timezone.now(),
            is_locked=True,
            locked_at=timezone.now(),
        )
        self.source = SourceVideo.objects.create(project=self.project, title="Source", youtube_video_id="dQw4w9WgXcQ", youtube_url="https://youtu.be/dQw4w9WgXcQ")
        self.chunk = TranscriptChunk.objects.create(source_video=self.source, sequence=1, start_seconds=0, duration_seconds=1, text="Original")
        self.client.force_login(self.admin_user)

    def test_admin_can_inspect_but_cannot_edit_add_or_delete_workflow_records(self):
        for model, obj in ((VideoProject, self.project), (SourceVideo, self.source), (TranscriptChunk, self.chunk)):
            prefix = f"admin:{model._meta.app_label}_{model._meta.model_name}"
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(reverse(f"{prefix}_change", args=[obj.pk])).status_code, 200)
                self.assertEqual(self.client.post(reverse(f"{prefix}_change", args=[obj.pk]), {"text": "Changed", "title": "Changed", "status": "draft"}).status_code, 403)
                self.assertEqual(self.client.post(reverse(f"{prefix}_add"), {}).status_code, 403)
                self.assertEqual(self.client.post(reverse(f"{prefix}_delete", args=[obj.pk]), {"post": "yes"}).status_code, 403)
                self.client.post(reverse(f"{prefix}_changelist"), {"action": "delete_selected", "_selected_action": [obj.pk], "post": "yes"})
                self.assertTrue(model.objects.filter(pk=obj.pk).exists())
        self.chunk.refresh_from_db()
        self.assertEqual(self.chunk.text, "Original")

    def test_admin_inlines_cannot_mutate_or_delete_rows(self):
        request = RequestFactory().post("/admin/")
        request.user = self.admin_user
        for inline in (SourceVideoInline(VideoProject, admin.site), TranscriptChunkInline(SourceVideo, admin.site)):
            self.assertFalse(inline.has_add_permission(request))
            self.assertFalse(inline.has_change_permission(request))
            self.assertFalse(inline.has_delete_permission(request))


class ProjectWriteSerializationTests(TransactionTestCase):
    """Use a file-backed SQLite TEST.NAME to exercise busy-lock waiting."""

    def test_approval_serializes_a_competing_edit_and_delete(self):
        database_name = str(connection.settings_dict["NAME"])
        if connection.vendor == "sqlite" and (database_name == ":memory:" or "mode=memory" in database_name):
            self.skipTest("SQLite concurrency requires a file-backed test database; shared-memory locks do not wait.")
        user = get_user_model().objects.create_user(username="concurrent-owner")
        project = VideoProject.objects.create(owner=user, title="Before", status=VideoProject.Status.TRANSCRIPT_READY)
        source = SourceVideo.objects.create(project=project, title="Source", youtube_video_id="dQw4w9WgXcQ", youtube_url="https://youtu.be/dQw4w9WgXcQ", transcript_status=SourceVideo.TranscriptStatus.COMPLETED)
        TranscriptChunk.objects.create(source_video=source, sequence=1, start_seconds=0, duration_seconds=1, text="Transcript")
        acquired, release = Event(), Event()
        outcomes = Queue()
        blocked_finished = Event()

        def approve():
            try:
                with transaction.atomic():
                    lock_project(project.pk)
                    acquired.set()
                    if not release.wait(5):
                        raise RuntimeError("Timed out waiting to release project lock")
                    ProjectWorkflowService.approve(project, user)
                outcomes.put("approved")
            except Exception as exc:
                outcomes.put(exc)
            finally:
                connections.close_all()

        def late_mutations():
            try:
                if not acquired.wait(5):
                    raise RuntimeError("Approval did not acquire its lock")
                for mutation in (
                    lambda: ProjectWorkflowService.update(project, {"title": "Late"}, user),
                    lambda: ProjectWorkflowService.delete_source(source, user),
                    lambda: ProjectWorkflowService.delete(project, user),
                ):
                    try:
                        mutation()
                    except ProjectWorkflowError:
                        continue
                    raise AssertionError("A protected mutation unexpectedly succeeded")
                outcomes.put("rejected")
            except Exception as exc:
                outcomes.put(exc)
            finally:
                blocked_finished.set()
                connections.close_all()

        threads = [Thread(target=approve), Thread(target=late_mutations)]
        try:
            threads[0].start()
            self.assertTrue(acquired.wait(5))
            threads[1].start()
            self.assertFalse(blocked_finished.wait(0.1), "Competing write bypassed the held project lock")
        finally:
            release.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        results = [outcomes.get_nowait(), outcomes.get_nowait()]
        self.assertCountEqual(results, ["approved", "rejected"])
        project.refresh_from_db()
        self.assertEqual(project.title, "Before")
        self.assertEqual(project.status, VideoProject.Status.APPROVED)
        self.assertTrue(SourceVideo.objects.filter(pk=source.pk).exists())
