from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from .models import SourceVideo, TranscriptChunk, VideoProject
from .services import (
    DuplicateSourceError,
    InvalidVideoInputError,
    ProjectMutationForbiddenError,
    ProjectWorkflowError,
    ProjectWorkflowService,
    TranscriptRemoteError,
    TranscriptService,
    TranscriptUnavailableError,
)


class TranscriptServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner", password="password")
        self.project = VideoProject.objects.create(owner=self.user, title="Project")
        self.transcript_client = Mock()
        self.transcript_client.fetch.return_value = [
            SimpleNamespace(start=0.0, duration=1.5, text="First"),
            SimpleNamespace(start=1.5, duration=2.0, text="Second"),
        ]
        self.metadata_client = Mock()
        self.metadata_client.fetch_title.return_value = "Fetched title"
        self.service = TranscriptService(self.transcript_client, self.metadata_client)

    def test_normalizes_supported_urls_and_raw_id(self):
        variants = [
            "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/shorts/dQw4w9WgXcQ?feature=share",
            "https://youtube.com/embed/dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?t=5",
        ]
        for value in variants:
            with self.subTest(value=value):
                self.assertEqual(TranscriptService.normalize_video_id(value), "dQw4w9WgXcQ")
        for invalid in ("", "too-short", "invalid id!", "https://example.com/dQw4w9WgXcQ"):
            with self.subTest(invalid=invalid), self.assertRaises(InvalidVideoInputError):
                TranscriptService.normalize_video_id(invalid)

    def test_success_persists_one_source_and_ordered_chunks(self):
        result = self.service.ingest(
            self.project,
            "https://youtu.be/dQw4w9WgXcQ?t=5",
        )
        self.assertEqual(result.chunks_created, 2)
        self.assertFalse(result.title_fallback_used)
        self.assertEqual(result.source_video.title, "Fetched title")
        self.assertEqual(result.source_video.youtube_video_id, "dQw4w9WgXcQ")
        self.assertEqual(
            list(result.source_video.transcript_chunks.values_list("sequence", "text")),
            [(1, "First"), (2, "Second")],
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.TRANSCRIPT_READY)

    def test_duplicate_variant_is_typed_and_does_not_fetch(self):
        first = self.service.ingest(self.project, "https://youtu.be/dQw4w9WgXcQ")
        self.transcript_client.reset_mock()
        with self.assertRaises(DuplicateSourceError) as raised:
            self.service.ingest(
                self.project,
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            )
        self.assertEqual(raised.exception.source_video.pk, first.source_video.pk)
        self.transcript_client.fetch.assert_not_called()
        self.assertEqual(SourceVideo.objects.count(), 1)
        self.assertEqual(TranscriptChunk.objects.count(), 2)

    def test_same_video_is_allowed_in_another_project(self):
        self.service.ingest(self.project, "dQw4w9WgXcQ")
        other = VideoProject.objects.create(owner=self.user, title="Other")
        result = self.service.ingest(other, "dQw4w9WgXcQ")
        self.assertEqual(SourceVideo.objects.filter(youtube_video_id="dQw4w9WgXcQ").count(), 2)
        self.assertEqual(result.source_video.project, other)

    def test_title_failure_uses_video_id_without_losing_transcript(self):
        self.metadata_client.fetch_title.side_effect = ValueError("malformed")
        result = self.service.ingest(self.project, "dQw4w9WgXcQ")
        self.assertTrue(result.title_fallback_used)
        self.assertEqual(result.source_video.title, "dQw4w9WgXcQ")
        self.assertEqual(result.source_video.transcript_chunks.count(), 2)

    def test_remote_empty_and_malformed_transcripts_leave_no_partial_rows(self):
        cases = [
            (RuntimeError("remote"), TranscriptRemoteError),
            ([], TranscriptUnavailableError),
            ([{"start": -1, "duration": 1, "text": "bad"}], TranscriptRemoteError),
        ]
        for fetched, expected_error in cases:
            with self.subTest(expected_error=expected_error.__name__):
                self.transcript_client.fetch.side_effect = fetched if isinstance(fetched, Exception) else None
                if not isinstance(fetched, Exception):
                    self.transcript_client.fetch.return_value = fetched
                with self.assertRaises(expected_error):
                    self.service.ingest(self.project, "dQw4w9WgXcQ")
                self.assertFalse(SourceVideo.objects.exists())
                self.assertFalse(TranscriptChunk.objects.exists())

    def test_persistence_failure_rolls_back_source(self):
        with patch.object(TranscriptChunk.objects, "bulk_create", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                self.service.ingest(self.project, "dQw4w9WgXcQ")
        self.assertFalse(SourceVideo.objects.exists())
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.DRAFT)

    def test_approved_locked_and_archived_projects_reject_ingestion(self):
        projects = [
            VideoProject.objects.create(
                owner=self.user,
                title="Approved",
                status=VideoProject.Status.APPROVED,
                approved_at=timezone.now(),
            ),
            VideoProject.objects.create(
                owner=self.user,
                title="Locked",
                status=VideoProject.Status.APPROVED,
                approved_at=timezone.now(),
                is_locked=True,
                locked_at=timezone.now(),
            ),
            VideoProject.objects.create(
                owner=self.user,
                title="Archived",
                status=VideoProject.Status.ARCHIVED,
                archived_at=timezone.now(),
            ),
        ]
        for project in projects:
            with self.subTest(project=project.title), self.assertRaises(
                ProjectMutationForbiddenError
            ):
                self.service.ingest(project, "dQw4w9WgXcQ")

    def test_database_constraints_protect_duplicates(self):
        source = SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://youtu.be/dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="First",
        )
        TranscriptChunk.objects.create(
            source_video=source,
            sequence=1,
            start_seconds=0,
            duration_seconds=1,
            text="First",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceVideo.objects.create(
                project=self.project,
                youtube_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
                youtube_video_id="dQw4w9WgXcQ",
                title="Duplicate",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TranscriptChunk.objects.create(
                source_video=source,
                sequence=1,
                start_seconds=1,
                duration_seconds=1,
                text="Duplicate",
            )

    def test_uniqueness_race_is_translated_to_duplicate_outcome(self):
        raced_source = SimpleNamespace(pk=99)
        first_lookup = Mock()
        first_lookup.first.return_value = None
        second_lookup = Mock()
        second_lookup.first.return_value = raced_source
        with (
            patch.object(SourceVideo.objects, "filter", side_effect=[first_lookup, second_lookup]),
            patch.object(SourceVideo.objects, "create", side_effect=IntegrityError("race")),
        ):
            with self.assertRaises(DuplicateSourceError) as raised:
                self.service.ingest(self.project, "dQw4w9WgXcQ")
        self.assertIs(raised.exception.source_video, raced_source)


class ProjectWorkflowServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="workflow", password="password")
        self.project = VideoProject.objects.create(owner=self.user, title="Workflow")
        self.source = SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://youtu.be/dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="Source",
            transcript_status=SourceVideo.TranscriptStatus.COMPLETED,
        )
        TranscriptChunk.objects.create(
            source_video=self.source,
            sequence=1,
            start_seconds=0,
            duration_seconds=1,
            text="Transcript",
        )
        self.project.status = VideoProject.Status.TRANSCRIPT_READY
        self.project.save(update_fields=["status"])

    def test_approve_lock_unlock_and_archive(self):
        project = ProjectWorkflowService.approve(self.project)
        self.assertIsNotNone(project.approved_at)
        project = ProjectWorkflowService.lock(project)
        self.assertTrue(project.is_locked)
        self.assertIsNotNone(project.locked_at)
        with self.assertRaises(ProjectWorkflowError):
            ProjectWorkflowService.archive(project)
        project = ProjectWorkflowService.unlock(project)
        self.assertFalse(project.is_locked)
        self.assertIsNone(project.locked_at)
        self.assertEqual(project.status, VideoProject.Status.APPROVED)
        project = ProjectWorkflowService.archive(project)
        self.assertEqual(project.status, VideoProject.Status.ARCHIVED)
        self.assertIsNotNone(project.archived_at)
        self.assertIsNotNone(project.approved_at)
        with self.assertRaises(ProjectWorkflowError):
            ProjectWorkflowService.archive(project)

    def test_approval_requires_complete_sources(self):
        self.source.transcript_status = SourceVideo.TranscriptStatus.FAILED
        self.source.save(update_fields=["transcript_status"])
        with self.assertRaises(ProjectWorkflowError):
            ProjectWorkflowService.approve(self.project)

    def test_approved_and_archived_projects_are_not_deletable(self):
        approved = ProjectWorkflowService.approve(self.project)
        with self.assertRaises(ProjectWorkflowError):
            ProjectWorkflowService.ensure_deletable(approved)
        archived = ProjectWorkflowService.archive(approved)
        with self.assertRaises(ProjectWorkflowError):
            ProjectWorkflowService.ensure_deletable(archived)
