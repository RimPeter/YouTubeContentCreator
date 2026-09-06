import tempfile
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.db import transaction
from production.services.access import ProductionValidationError

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from production.models import MediaAsset
from production.services.cleanup import MediaCleanupService
from production.services.dependencies import ArtifactDependencyService
from scraper.models import SourceVideo

from .helpers import create_approved_project, create_validated_asset


class MediaCleanupTests(TestCase):
    def setUp(self):
        self.user, self.project = create_approved_project("cleanup-owner")
        self.temporary_media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.temporary_media.name)
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        self.temporary_media.cleanup()

    def old_asset(self, status, name):
        asset = MediaAsset.objects.create(
            project=self.project,
            kind=MediaAsset.Kind.OTHER,
            status=status,
            display_name=name,
            error_code="failed" if status == MediaAsset.Status.FAILED else "",
            created_by=self.user,
        )
        asset.file.save(f"{name}.mp4", ContentFile(b"old"))
        MediaAsset.objects.filter(pk=asset.pk).update(
            updated_at=timezone.now() - timedelta(days=40)
        )
        asset.refresh_from_db()
        return asset

    def test_cleanup_defaults_to_dry_run_and_never_removes_current_assets(self):
        removable = self.old_asset(MediaAsset.Status.FAILED, "failed")
        current = create_validated_asset(self.project, self.user, display_name="Current")
        output = StringIO()

        call_command("cleanup_media_assets", "--older-than-days", "30", stdout=output)

        self.assertTrue(MediaAsset.objects.filter(pk=removable.pk).exists())
        self.assertTrue(MediaAsset.objects.filter(pk=current.pk).exists())
        self.assertIn("Dry run only", output.getvalue())

    def test_execute_removes_only_unreferenced_failed_or_superseded_assets(self):
        removable = self.old_asset(MediaAsset.Status.SUPERSEDED, "superseded")
        protected = self.old_asset(MediaAsset.Status.FAILED, "protected")
        source = SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://youtu.be/dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="Cleanup source",
        )
        ArtifactDependencyService.link(
            self.project,
            source,
            protected,
            self.user,
            upstream_version=1,
            upstream_fingerprint="c" * 64,
            downstream_version=1,
            relation_type="generated_from",
        )
        removed_path = removable.file.path

        with self.captureOnCommitCallbacks(execute=True):
            removed = MediaCleanupService.cleanup(30, execute=True)

        self.assertEqual([item[0] for item in removed], [removable.pk])
        self.assertFalse(MediaAsset.objects.filter(pk=removable.pk).exists())
        self.assertFalse(removable.file.storage.exists(removable.file.name))
        self.assertTrue(MediaAsset.objects.filter(pk=protected.pk).exists())
        self.assertNotEqual(removed_path, "")


    def test_cleanup_retains_source_media_referenced_by_retired_clip_history(self):
        from .helpers import create_selected_segment, create_source_upload
        from production.services.clips import SourceClipService
        from analysis.services.selection import SelectionService
        user, project, _, selection = create_selected_segment("cleanup-history-owner")
        asset = create_source_upload(project, user)
        clip, _ = SourceClipService().enqueue_clip(selection, asset, user)
        SelectionService.deselect(project, selection, user)
        MediaAsset.objects.filter(pk=asset.pk).update(status=MediaAsset.Status.FAILED,
            updated_at=timezone.now() - timedelta(days=40))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(MediaCleanupService.cleanup(30, execute=True), [])
        self.assertTrue(MediaAsset.objects.filter(pk=asset.pk).exists())
        self.assertTrue(asset.file.storage.exists(asset.file.name))

    def test_cleanup_rechecks_new_dependency_before_deleting_candidate(self):
        asset = self.old_asset(MediaAsset.Status.FAILED, "late-reference")
        source = SourceVideo.objects.create(project=self.project,
            youtube_url="https://youtu.be/dQw4w9WgXcQ", youtube_video_id="dQw4w9WgXcQ", title="Source")
        ArtifactDependencyService.link(self.project, source, asset, self.user,
            upstream_version=1, upstream_fingerprint="a"*64, downstream_version=1,
            relation_type="generated_from")
        with patch.object(MediaCleanupService, "candidates", return_value=[asset]):
            self.assertEqual(MediaCleanupService.cleanup(30, execute=True), [])
        self.assertTrue(asset.file.storage.exists(asset.file.name))

    def test_outer_rollback_keeps_row_and_file(self):
        asset = self.old_asset(MediaAsset.Status.FAILED, "rollback")
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    MediaCleanupService.cleanup(30, execute=True)
                    raise RuntimeError("outer operation failed")
        self.assertTrue(MediaAsset.objects.filter(pk=asset.pk).exists())
        self.assertTrue(asset.file.storage.exists(asset.file.name))

    def test_late_dependency_cannot_reference_deleted_asset(self):
        asset = self.old_asset(MediaAsset.Status.FAILED, "deleted")
        source = SourceVideo.objects.create(project=self.project,
            youtube_url="https://youtu.be/dQw4w9WgXcQ", youtube_video_id="dQw4w9WgXcQ", title="Source")
        with self.captureOnCommitCallbacks(execute=True):
            MediaCleanupService.cleanup(30, execute=True)
        with self.assertRaises(ProductionValidationError):
            ArtifactDependencyService.link(self.project, source, asset, self.user,
                upstream_version=1, upstream_fingerprint="a"*64, downstream_version=1,
                relation_type="generated_from")
