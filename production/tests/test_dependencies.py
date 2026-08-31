from django.contrib.auth import get_user_model
from django.test import TestCase

from production.models import MediaAsset
from production.services.access import ProductionPermissionError, ProductionValidationError
from production.services.dependencies import ArtifactDependencyService
from scraper.models import SourceVideo

from .helpers import create_approved_project, create_validated_asset


class ArtifactDependencyServiceTests(TestCase):
    def setUp(self):
        self.user, self.project = create_approved_project("dependency-owner")
        self.other = get_user_model().objects.create_user(username="dependency-other")
        self.source = SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://youtu.be/dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="Dependency source",
            transcript_status=SourceVideo.TranscriptStatus.COMPLETED,
        )
        self.asset = create_validated_asset(self.project, self.user)

    def link(self, fingerprint="a" * 64, downstream=None):
        return ArtifactDependencyService.link(
            self.project,
            self.source,
            downstream or self.asset,
            self.user,
            upstream_version=1,
            upstream_fingerprint=fingerprint,
            downstream_version=1,
            relation_type="generated_from",
        )

    def test_link_is_auditable_and_idempotent(self):
        first, created = self.link()
        repeated, repeated_created = self.link()

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(first.upstream_object, self.source)
        self.assertEqual(first.downstream_object, self.asset)

    def test_link_rejects_cross_project_and_cross_user(self):
        with self.assertRaises(ProductionPermissionError):
            ArtifactDependencyService.link(
                self.project,
                self.source,
                self.asset,
                self.other,
                upstream_version=1,
                upstream_fingerprint="a" * 64,
                downstream_version=1,
                relation_type="generated_from",
            )
        other_user, other_project = create_approved_project("dependency-second-owner")
        other_asset = create_validated_asset(other_project, other_user)
        with self.assertRaises(ProductionValidationError):
            self.link(downstream=other_asset)

    def test_changed_fingerprint_stales_only_dependent_current_assets(self):
        unchanged = create_validated_asset(
            self.project, self.user, display_name="Unchanged"
        )
        self.link(fingerprint="a" * 64)
        self.link(fingerprint="b" * 64, downstream=unchanged)

        updated = ArtifactDependencyService.mark_media_dependents_stale(
            self.project, self.source, "b" * 64, self.user
        )

        self.assertEqual(updated, 1)
        self.asset.refresh_from_db()
        unchanged.refresh_from_db()
        self.assertEqual(self.asset.status, MediaAsset.Status.STALE)
        self.assertEqual(unchanged.status, MediaAsset.Status.VALIDATED)
