import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from production.models import MediaAsset, PipelineJob, SourceClip

from .helpers import create_selected_segment, create_source_upload


@override_settings(ALLOWED_HOSTS=["testserver"])
class ProductionViewTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, self.selection = create_selected_segment(
            "clip-view-owner"
        )
        self.other = get_user_model().objects.create_user(
            username="clip-view-other", password="password"
        )
        self.temporary_media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.temporary_media.name)
        self.media_override.enable()
        self.source_asset = create_source_upload(self.project, self.user)
        self.output_asset = MediaAsset(
            project=self.project,
            kind=MediaAsset.Kind.SOURCE_CLIP,
            status=MediaAsset.Status.VALIDATED,
            display_name="Preview clip.mp4",
            checksum_sha256="c" * 64,
            byte_size=16,
            duration_seconds=Decimal("2.000"),
            width=320,
            height=180,
            frame_rate=30,
            has_audio=True,
            has_video=True,
            detected_mime_type="video/mp4",
            container="mov,mp4",
            video_codec="h264",
            audio_codec="aac",
            origin=MediaAsset.Origin.GENERATED,
            rights_basis=MediaAsset.RightsBasis.USER_OWNED,
            consent_metadata={"rights_confirmed": True},
            created_by=self.user,
            validated_at=timezone.now(),
        )
        preview_bytes = b"0123456789abcdef"
        self.output_asset.file.save("preview.mp4", ContentFile(preview_bytes), save=False)
        self.output_asset.save()
        self.job = PipelineJob.objects.create(
            project=self.project,
            job_type="clip_trim",
            status=PipelineJob.Status.SUCCEEDED,
            progress=100,
            idempotency_key="d" * 64,
            requested_by=self.user,
            max_attempts=2,
            attempt_count=1,
            input_fingerprint="e" * 64,
            completed_at=timezone.now(),
        )
        self.clip = SourceClip.objects.create(
            project=self.project,
            selected_segment=self.selection,
            source_asset=self.source_asset,
            processed_asset=self.output_asset,
            pipeline_job=self.job,
            version=1,
            status=SourceClip.Status.VALIDATED,
            input_fingerprint="f" * 64,
            requested_start_seconds=1,
            requested_end_seconds=3,
            actual_start_seconds=1,
            actual_end_seconds=3,
            expected_duration_seconds=2,
            actual_duration_seconds=2,
            validation_result={"playable": True},
            created_by=self.user,
            validated_at=timezone.now(),
        )
        self.client.force_login(self.user)

    def tearDown(self):
        self.media_override.disable()
        self.temporary_media.cleanup()

    def test_dashboard_and_project_page_link(self):
        response = self.client.get(
            reverse("production:project_clips", args=[self.project.pk])
        )
        self.assertContains(response, "Upload authorized source media")
        self.assertContains(response, self.selection.analysis_segment.title)
        project_page = self.client.get(reverse("project_detail", args=[self.project.pk]))
        self.assertContains(
            project_page,
            reverse("production:project_clips", args=[self.project.pk]),
        )

    @patch("production.views.MediaAssetService")
    def test_upload_requires_rights_and_calls_service(self, service_class):
        service_class.return_value.create_upload.return_value = self.source_asset
        invalid = self.client.post(
            reverse("production:upload_source_media", args=[self.project.pk]),
            {
                "media_file": SimpleUploadedFile("source.mp4", b"media"),
                "rights_basis": MediaAsset.RightsBasis.USER_OWNED,
            },
        )
        self.assertRedirects(
            invalid, reverse("production:project_clips", args=[self.project.pk])
        )
        service_class.return_value.create_upload.assert_not_called()

        response = self.client.post(
            reverse("production:upload_source_media", args=[self.project.pk]),
            {
                "media_file": SimpleUploadedFile("source.mp4", b"media"),
                "rights_basis": MediaAsset.RightsBasis.USER_OWNED,
                "rights_confirmed": "on",
                "rights_notes": "I created this source.",
            },
        )
        self.assertRedirects(
            response, reverse("production:project_clips", args=[self.project.pk])
        )
        service_class.return_value.create_upload.assert_called_once()

    @patch("production.views.SourceClipService")
    def test_create_and_approve_are_post_only(self, service_class):
        service_class.return_value.create_clip.return_value = (self.clip, True)
        response = self.client.post(
            reverse("production:create_source_clip", args=[self.selection.pk]),
            {
                "source_asset": self.source_asset.pk,
                "padding_before_seconds": 0,
                "padding_after_seconds": 0,
            },
        )
        self.assertRedirects(
            response, reverse("production:clip_detail", args=[self.clip.pk])
        )
        self.assertEqual(
            self.client.get(
                reverse("production:create_source_clip", args=[self.selection.pk])
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(
                reverse("production:approve_source_clip", args=[self.clip.pk])
            ).status_code,
            405,
        )

    def test_preview_is_owner_only_and_supports_byte_ranges(self):
        response = self.client.get(
            reverse("production:stream_source_clip", args=[self.clip.pk]),
            HTTP_RANGE="bytes=2-5",
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], "bytes 2-5/16")
        self.assertEqual(b"".join(response.streaming_content), b"2345")
        self.assertEqual(response["Cache-Control"], "private, no-store")

        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("production:clip_detail", args=[self.clip.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("production:stream_source_clip", args=[self.clip.pk])
            ).status_code,
            404,
        )

    def test_csrf_is_required_for_mutations(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(
            csrf_client.post(
                reverse("production:approve_source_clip", args=[self.clip.pk])
            ).status_code,
            403,
        )
