import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from production.models import MediaAsset
from production.services.access import ProductionLifecycleError, ProductionPermissionError
from production.services.media_assets import MediaAssetService, MediaAssetValidationError
from production.services.probe import MediaProbeError

from .helpers import FakeProbeClient, create_approved_project


class MediaAssetServiceTests(TestCase):
    def setUp(self):
        self.user, self.project = create_approved_project("media-owner")
        self.other = get_user_model().objects.create_user(username="media-other")
        self.temporary_media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.temporary_media.name,
            PRODUCTION_MAX_UPLOAD_BYTES=20,
            PRODUCTION_ALLOWED_MEDIA_EXTENSIONS=(".mp4", ".wav"),
        )
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        self.temporary_media.cleanup()

    @staticmethod
    def upload(name="../../private-video.mp4", content=b"valid-media"):
        return SimpleUploadedFile(name, content, content_type="application/octet-stream")

    def test_upload_is_hashed_probed_and_stored_under_isolated_key(self):
        probe = FakeProbeClient()
        asset = MediaAssetService(probe).create_upload(
            self.project,
            self.upload(),
            self.user,
            rights_basis=MediaAsset.RightsBasis.USER_OWNED,
        )

        self.assertEqual(asset.status, MediaAsset.Status.VALIDATED)
        self.assertEqual(asset.byte_size, 11)
        self.assertEqual(asset.checksum_sha256, "9b476f5a168247b1fbf06a78ffe9857d99e900fee4fbbc1cc2eb00e34b924191")
        self.assertTrue(asset.file.name.startswith(f"projects/{self.project.pk}/source_upload/"))
        self.assertNotIn("private-video", asset.file.name)
        self.assertTrue(os.path.exists(asset.file.path))
        self.assertEqual(probe.calls[0][1], ".mp4")

    def test_upload_rejects_unauthorized_locked_bad_extension_and_large_files(self):
        service = MediaAssetService(FakeProbeClient())
        with self.assertRaises(ProductionPermissionError):
            service.create_upload(self.project, self.upload(), self.other)

        self.project.is_locked = True
        self.project.locked_at = self.project.approved_at
        self.project.save(update_fields=["is_locked", "locked_at"])
        with self.assertRaises(ProductionLifecycleError):
            service.create_upload(self.project, self.upload(), self.user)
        self.project.is_locked = False
        self.project.locked_at = None
        self.project.save(update_fields=["is_locked", "locked_at"])

        with self.assertRaises(MediaAssetValidationError) as caught:
            service.create_upload(self.project, self.upload("malware.exe"), self.user)
        self.assertEqual(caught.exception.code, "extension_not_allowed")
        with self.assertRaises(MediaAssetValidationError) as caught:
            service.create_upload(self.project, self.upload(content=b"x" * 21), self.user)
        self.assertEqual(caught.exception.code, "upload_too_large")

    def test_probe_failure_leaves_no_asset_or_stored_file(self):
        service = MediaAssetService(
            FakeProbeClient(error=MediaProbeError("invalid_media", "Invalid media."))
        )
        with self.assertRaises(MediaAssetValidationError) as caught:
            service.create_upload(self.project, self.upload(), self.user)
        self.assertEqual(caught.exception.code, "invalid_media")
        self.assertEqual(MediaAsset.objects.count(), 0)
        self.assertEqual(list(os.scandir(self.temporary_media.name)), [])

    def test_new_version_retains_lineage_and_approval_is_explicit(self):
        service = MediaAssetService(FakeProbeClient())
        first = service.create_upload(self.project, self.upload(), self.user)
        first = service.approve(first, self.user)
        second = service.create_upload(
            self.project,
            self.upload(content=b"new-content"),
            self.user,
            previous_asset=first,
        )
        self.assertEqual(first.lineage_id, second.lineage_id)
        self.assertEqual(second.version, 2)
        approved = service.approve(second, self.user)
        self.assertEqual(approved.status, MediaAsset.Status.APPROVED)
        self.assertIsNotNone(approved.approved_at)
        first.refresh_from_db()
        self.assertEqual(first.status, MediaAsset.Status.SUPERSEDED)
        self.assertTrue(first.file.storage.exists(first.file.name))
