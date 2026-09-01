import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from analysis.services.selection import SelectionService
from production.models import ArtifactDependency, MediaAsset, PipelineJob, SourceClip
from production.services.access import ProductionLifecycleError, ProductionPermissionError
from production.services.clips import SourceClipError, SourceClipService
from production.services.ffmpeg import FFmpegError
from production.services.media_assets import MediaAssetService
from production.services.probe import MediaMetadata

from .helpers import (
    FakeProbeClient,
    create_selected_segment,
    create_source_upload,
)


class FakeFFmpegClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def trim(self, input_path, output_path, start_seconds, end_seconds):
        self.calls.append((input_path, output_path, start_seconds, end_seconds))
        if self.error:
            raise self.error
        Path(output_path).write_bytes(b"generated-clip")


@override_settings(
    PRODUCTION_MAX_UPLOAD_BYTES=1024 * 1024,
    PRODUCTION_ALLOWED_MEDIA_EXTENSIONS=(".mp4",),
    SOURCE_CLIP_DURATION_TOLERANCE_SECONDS=0.35,
)
class SourceClipServiceTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, self.selection = create_selected_segment()
        self.other = get_user_model().objects.create_user(username="clip-other")
        self.temporary_media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.temporary_media.name)
        self.media_override.enable()
        self.asset = create_source_upload(self.project, self.user)
        metadata = MediaMetadata(
            duration_seconds=Decimal("2.000"),
            width=320,
            height=180,
            frame_rate=Decimal("30"),
            has_audio=True,
            has_video=True,
            detected_mime_type="video/mp4",
            container="mov,mp4",
            video_codec="h264",
            audio_codec="aac",
        )
        self.ffmpeg = FakeFFmpegClient()
        self.service = SourceClipService(
            ffmpeg_client=self.ffmpeg,
            media_service=MediaAssetService(FakeProbeClient(metadata=metadata)),
        )

    def tearDown(self):
        self.media_override.disable()
        self.temporary_media.cleanup()

    def test_create_clip_persists_validated_output_job_and_dependencies(self):
        clip, created = self.service.create_clip(
            self.selection, self.asset, self.user
        )

        self.assertTrue(created)
        self.assertEqual(clip.status, SourceClip.Status.VALIDATED)
        self.assertEqual(clip.actual_start_seconds, Decimal("1.000"))
        self.assertEqual(clip.actual_end_seconds, Decimal("3.000"))
        self.assertEqual(clip.processed_asset.kind, MediaAsset.Kind.SOURCE_CLIP)
        self.assertEqual(clip.processed_asset.origin, MediaAsset.Origin.GENERATED)
        self.assertEqual(clip.pipeline_job.status, PipelineJob.Status.SUCCEEDED)
        self.assertEqual(ArtifactDependency.objects.count(), 2)
        self.assertTrue(clip.processed_asset.file.storage.exists(clip.processed_asset.file.name))

        repeated, repeated_created = self.service.create_clip(
            self.selection, self.asset, self.user
        )
        self.assertFalse(repeated_created)
        self.assertEqual(repeated.pk, clip.pk)
        self.assertEqual(SourceClip.objects.count(), 1)

    def test_padding_is_clamped_to_source_and_recorded(self):
        self.service.media_service = MediaAssetService(
            FakeProbeClient(
                metadata=MediaMetadata(
                    duration_seconds=Decimal("5.000"),
                    width=320,
                    height=180,
                    frame_rate=Decimal("30"),
                    has_audio=True,
                    has_video=True,
                    detected_mime_type="video/mp4",
                    container="mov,mp4",
                    video_codec="h264",
                    audio_codec="aac",
                )
            )
        )
        clip, _ = self.service.create_clip(
            self.selection,
            self.asset,
            self.user,
            padding_before=2,
            padding_after=3,
        )
        self.assertEqual(clip.actual_start_seconds, Decimal("0"))
        self.assertEqual(clip.actual_end_seconds, Decimal("5.000"))
        self.assertEqual(self.ffmpeg.calls[0][2:], (Decimal("0"), Decimal("5.000")))

    def test_rights_bounds_permissions_and_lock_are_enforced(self):
        with self.assertRaises(ProductionPermissionError):
            self.service.create_clip(self.selection, self.asset, self.other)

        self.asset.consent_metadata = {}
        self.asset.save(update_fields=["consent_metadata"])
        with self.assertRaises(SourceClipError) as caught:
            self.service.create_clip(self.selection, self.asset, self.user)
        self.assertEqual(caught.exception.code, "rights_not_confirmed")
        self.asset.consent_metadata = {"rights_confirmed": True}
        self.asset.duration_seconds = Decimal("2.000")
        self.asset.save(update_fields=["consent_metadata", "duration_seconds"])
        with self.assertRaises(SourceClipError):
            self.service.create_clip(self.selection, self.asset, self.user)

        self.asset.duration_seconds = Decimal("5.000")
        self.asset.save(update_fields=["duration_seconds"])
        self.project.is_locked = True
        self.project.locked_at = self.project.approved_at
        self.project.save(update_fields=["is_locked", "locked_at"])
        with self.assertRaises(ProductionLifecycleError):
            self.service.create_clip(self.selection, self.asset, self.user)

    def test_ffmpeg_failure_is_audited_without_output_asset(self):
        self.service.ffmpeg_client = FakeFFmpegClient(
            FFmpegError("clip_trim_failed", "FFmpeg could not create the source clip.")
        )
        with self.assertRaises(SourceClipError):
            self.service.create_clip(self.selection, self.asset, self.user)

        clip = SourceClip.objects.get()
        job = PipelineJob.objects.get()
        self.assertEqual(clip.status, SourceClip.Status.FAILED)
        self.assertEqual(clip.error_code, "clip_trim_failed")
        self.assertEqual(job.status, PipelineJob.Status.FAILED)
        self.assertEqual(
            MediaAsset.objects.filter(kind=MediaAsset.Kind.SOURCE_CLIP).count(), 0
        )

    @patch("production.services.clips.ArtifactDependencyService.link")
    def test_dependency_failure_rolls_back_clip_and_marks_output_failed(self, link):
        link.side_effect = RuntimeError("database failure")
        with self.assertRaises(SourceClipError) as caught:
            self.service.create_clip(self.selection, self.asset, self.user)
        self.assertEqual(caught.exception.code, "clip_processing_failed")
        clip = SourceClip.objects.get()
        output = MediaAsset.objects.get(kind=MediaAsset.Kind.SOURCE_CLIP)
        self.assertEqual(clip.status, SourceClip.Status.FAILED)
        self.assertEqual(output.status, MediaAsset.Status.FAILED)
        self.assertEqual(ArtifactDependency.objects.count(), 0)
        self.assertEqual(PipelineJob.objects.get().status, PipelineJob.Status.FAILED)

    def test_approval_and_regeneration_supersede_prior_versions(self):
        first, _ = self.service.create_clip(self.selection, self.asset, self.user)
        first = SourceClipService.approve(first, self.user)
        self.assertEqual(first.status, SourceClip.Status.APPROVED)
        self.assertEqual(first.processed_asset.status, MediaAsset.Status.APPROVED)

        self.service.media_service = MediaAssetService(
            FakeProbeClient(
                metadata=MediaMetadata(
                    duration_seconds=Decimal("3.000"),
                    width=320,
                    height=180,
                    frame_rate=Decimal("30"),
                    has_audio=True,
                    has_video=True,
                    detected_mime_type="video/mp4",
                    container="mov,mp4",
                    video_codec="h264",
                    audio_codec="aac",
                )
            )
        )
        second, _ = self.service.create_clip(
            self.selection, self.asset, self.user, padding_after=1
        )
        second = SourceClipService.approve(second, self.user)
        first.refresh_from_db()
        first.processed_asset.refresh_from_db()
        self.assertEqual(second.version, 2)
        self.assertEqual(first.status, SourceClip.Status.SUPERSEDED)
        self.assertEqual(first.processed_asset.status, MediaAsset.Status.SUPERSEDED)

    def test_changed_selection_is_marked_stale_on_approval(self):
        clip, _ = self.service.create_clip(self.selection, self.asset, self.user)
        self.selection.reviewed_end_seconds = 2.5
        self.selection.save(update_fields=["reviewed_end_seconds", "updated_at"])

        with self.assertRaises(SourceClipError):
            SourceClipService.approve(clip, self.user)

        clip.refresh_from_db()
        clip.processed_asset.refresh_from_db()
        self.assertEqual(clip.status, SourceClip.Status.STALE)
        self.assertEqual(clip.processed_asset.status, MediaAsset.Status.STALE)

    def test_selection_service_immediately_invalidates_boundary_changes_only(self):
        clip, _ = self.service.create_clip(self.selection, self.asset, self.user)
        clip = SourceClipService.approve(clip, self.user)

        SelectionService.select(
            self.project,
            self.selection.analysis_segment,
            self.user,
            reviewed_start=1,
            reviewed_end=3,
            notes="Editorial note only",
        )
        clip.refresh_from_db()
        clip.processed_asset.refresh_from_db()
        self.assertEqual(clip.status, SourceClip.Status.APPROVED)
        self.assertEqual(clip.processed_asset.status, MediaAsset.Status.APPROVED)

        SelectionService.select(
            self.project,
            self.selection.analysis_segment,
            self.user,
            reviewed_start=1,
            reviewed_end=2.5,
            notes="Boundary changed",
        )
        clip.refresh_from_db()
        clip.processed_asset.refresh_from_db()
        self.assertEqual(clip.status, SourceClip.Status.STALE)
        self.assertEqual(clip.processed_asset.status, MediaAsset.Status.STALE)
