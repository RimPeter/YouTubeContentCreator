import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase, override_settings

from production.models import MediaAsset
from production.services.access import ProductionLifecycleError
from production.services.media_assets import MediaAssetService
from production.tests.helpers import create_approved_project, FakeProbeClient


class MediaTransactionTests(TransactionTestCase):
    def test_copy_precedes_write_transaction_and_failed_attachment_cleans_file(self):
        user, project = create_approved_project("media-transaction")
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            save = default_storage.save
            copied = []
            def copy(name, content, **kwargs):
                self.assertFalse(connection.in_atomic_block)
                stored = save(name, content, **kwargs)
                copied.append(stored)
                return stored
            service = MediaAssetService(FakeProbeClient())
            with patch.object(default_storage, "save", side_effect=copy):
                asset = service.create_upload(project, SimpleUploadedFile("source.mp4", b"video"), user)
                path = Path(directory) / "generated.mp4"
                path.write_bytes(b"generated")
                output = service.create_generated_file(project, path, user, kind=MediaAsset.Kind.SOURCE_CLIP,
                    display_name="Generated", rights_basis="user_owned")
            self.assertTrue(default_storage.exists(asset.file.name))
            self.assertTrue(default_storage.exists(output.file.name))
            def lock_after_copy(name, content, **kwargs):
                stored = copy(name, content, **kwargs)
                type(project).objects.filter(pk=project.pk).update(is_locked=True)
                return stored
            with patch.object(default_storage, "save", side_effect=lock_after_copy):
                with self.assertRaises(ProductionLifecycleError):
                    service.create_upload(project, SimpleUploadedFile("source.mp4", b"late"), user)
            self.assertFalse(default_storage.exists(copied[-1]))
            self.assertEqual(MediaAsset.objects.count(), 2)
