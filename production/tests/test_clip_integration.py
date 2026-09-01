import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from production.models import MediaAsset, PipelineJob, SourceClip
from production.services.clips import SourceClipService
from production.services.media_assets import MediaAssetService

from .helpers import create_selected_segment


FFMPEG_AVAILABLE = bool(shutil.which(settings.FFMPEG_EXECUTABLE))
FFPROBE_AVAILABLE = bool(shutil.which(settings.FFPROBE_EXECUTABLE))


@skipUnless(
    FFMPEG_AVAILABLE and FFPROBE_AVAILABLE,
    "FFmpeg and FFprobe are required for the Phase D integration test.",
)
class SourceClipFFmpegIntegrationTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, self.selection = create_selected_segment(
            "clip-integration-owner"
        )
        self.temporary_media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.temporary_media.name)
        self.media_override.enable()

    def tearDown(self):
        self.media_override.disable()
        self.temporary_media.cleanup()

    def test_real_tiny_video_upload_trim_probe_and_persist(self):
        fixture_path = Path(self.temporary_media.name) / "fixture-input.mp4"
        result = subprocess.run(
            [
                settings.FFMPEG_EXECUTABLE,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=10",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-t",
                "4",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(fixture_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            shell=False,
        )
        self.assertEqual(result.returncode, 0, "Tiny fixture generation failed.")
        upload = SimpleUploadedFile("authorized.mp4", fixture_path.read_bytes())
        source_asset = MediaAssetService().create_upload(
            self.project,
            upload,
            self.user,
            rights_basis=MediaAsset.RightsBasis.USER_OWNED,
            rights_notes="Generated locally for the deterministic test.",
            consent_metadata={"rights_confirmed": True},
        )

        clip, created = SourceClipService().create_clip(
            self.selection,
            source_asset,
            self.user,
        )

        self.assertTrue(created)
        self.assertEqual(clip.status, SourceClip.Status.VALIDATED)
        self.assertEqual(clip.pipeline_job.status, PipelineJob.Status.SUCCEEDED)
        self.assertTrue(clip.processed_asset.has_video)
        self.assertTrue(clip.processed_asset.has_audio)
        self.assertEqual(clip.processed_asset.width, 160)
        self.assertAlmostEqual(float(clip.actual_duration_seconds), 2.0, delta=0.35)
        self.assertTrue(clip.processed_asset.file.storage.exists(clip.processed_asset.file.name))
