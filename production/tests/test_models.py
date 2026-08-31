from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from production.models import MediaAsset, PipelineJob, project_media_upload_to

from .helpers import create_approved_project, create_validated_asset


class ProductionModelTests(TestCase):
    def setUp(self):
        self.user, self.project = create_approved_project()

    def test_storage_key_is_project_scoped_and_does_not_trust_basename(self):
        asset = MediaAsset(project=self.project, kind=MediaAsset.Kind.SOURCE_UPLOAD)

        key = project_media_upload_to(asset, "../../private-secret.MP4")

        self.assertTrue(key.startswith(f"projects/{self.project.pk}/source_upload/"))
        self.assertTrue(key.endswith(".mp4"))
        self.assertNotIn("private-secret", key)
        self.assertNotIn("..", key)

    def test_validated_and_failed_assets_require_lifecycle_metadata(self):
        validated = MediaAsset(
            project=self.project,
            kind=MediaAsset.Kind.SOURCE_UPLOAD,
            status=MediaAsset.Status.VALIDATED,
            display_name="Missing metadata",
        )
        with self.assertRaises(ValidationError):
            validated.full_clean()

        failed = MediaAsset(
            project=self.project,
            kind=MediaAsset.Kind.SOURCE_UPLOAD,
            status=MediaAsset.Status.FAILED,
            display_name="Failed",
        )
        with self.assertRaises(ValidationError):
            failed.full_clean()

    def test_asset_lineage_version_is_unique(self):
        first = create_validated_asset(self.project, self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_validated_asset(
                self.project,
                self.user,
                display_name="Duplicate",
                lineage_id=first.lineage_id,
            )

    def test_active_job_idempotency_and_progress_constraints(self):
        values = {
            "project": self.project,
            "job_type": "clip_trim",
            "idempotency_key": "b" * 64,
            "requested_by": self.user,
            "input_fingerprint": "c" * 64,
        }
        PipelineJob.objects.create(**values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PipelineJob.objects.create(**values)
        invalid_values = {**values, "idempotency_key": "d" * 64, "progress": 101}
        with self.assertRaises(IntegrityError), transaction.atomic():
            PipelineJob.objects.create(**invalid_values)

    def test_successful_job_validation_requires_completion_and_full_progress(self):
        job = PipelineJob(
            project=self.project,
            job_type="clip_trim",
            status=PipelineJob.Status.SUCCEEDED,
            idempotency_key="e" * 64,
            input_fingerprint="f" * 64,
            requested_by=self.user,
            progress=50,
        )
        with self.assertRaises(ValidationError):
            job.full_clean()
        job.progress = 100
        job.completed_at = timezone.now()
        job.full_clean()
