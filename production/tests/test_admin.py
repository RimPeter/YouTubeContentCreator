from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from production.models import MediaAsset, PipelineJob, SourceClip
from .helpers import create_selected_segment, create_source_upload


@override_settings(ALLOWED_HOSTS=["testserver"], STORAGES={
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class ProductionAdminIntegrityTests(TestCase):
    def setUp(self):
        self.user, self.project, _, _ = create_selected_segment("media-admin-owner")
        self.asset = create_source_upload(self.project, self.user)
        self.admin_user = get_user_model().objects.create_superuser("media-admin", password="test-password")
        self.client.force_login(self.admin_user)

    def test_admin_cannot_replace_media_or_mutate_its_metadata(self):
        original_name = self.asset.file.name
        original_checksum = self.asset.checksum_sha256
        response = self.client.post(reverse("admin:production_mediaasset_change", args=[self.asset.pk]), {
            "file": SimpleUploadedFile("replacement.mp4", b"This is not a video."),
            "status": "approved", "version": 999, "display_name": "Replacement",
        })
        self.assertEqual(response.status_code, 403)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.file.name, original_name)
        self.assertEqual(self.asset.checksum_sha256, original_checksum)
        self.assertEqual(self.asset.status, MediaAsset.Status.VALIDATED)
        self.assertEqual(self.asset.version, 1)

    def test_admin_add_delete_and_bulk_delete_are_disabled(self):
        for model in [MediaAsset, PipelineJob, SourceClip]:
            response = self.client.post(reverse(f"admin:production_{model._meta.model_name}_add"), {})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:production_mediaasset_delete", args=[self.asset.pk]),
                                         {"post": "yes"}).status_code, 403)
        self.client.post(reverse("admin:production_mediaasset_changelist"), {
            "action": "delete_selected", "_selected_action": self.asset.pk, "post": "yes",
        })
        self.assertTrue(MediaAsset.objects.filter(pk=self.asset.pk).exists())
