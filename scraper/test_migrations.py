import importlib

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LegacyHistoryMigrationTests(TransactionTestCase):
    migrate_from = ("scraper", "0003_scrapedvideo_video_title")
    migrate_to = ("scraper", "0005_import_legacy_history")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        ScrapedVideo = old_apps.get_model("scraper", "ScrapedVideo")
        TranscriptEntry = old_apps.get_model("scraper", "TranscriptEntry")

        self.complete = ScrapedVideo.objects.create(
            youtube_video_id="dQw4w9WgXcQ",
            youtube_url="https://youtu.be/dQw4w9WgXcQ?t=10",
            video_title="Unicode — complete",
        )
        first = TranscriptEntry.objects.create(
            scraped_video=self.complete,
            sequence=1,
            start_seconds=0.0,
            duration_seconds=1.5,
            text="First ✓",
        )
        TranscriptEntry.objects.create(
            scraped_video=self.complete,
            sequence=3,
            start_seconds=2.0,
            duration_seconds=3.5,
            text="Long " + ("transcript " * 2000),
        )
        self.complete_created_at = self.complete.created_at
        self.first_entry_created_at = first.created_at

        self.empty = ScrapedVideo.objects.create(
            youtube_video_id="jwXITbC9pVI",
            youtube_url="https://www.youtube.com/watch?v=jwXITbC9pVI",
            video_title="",
        )
        self.mismatch = ScrapedVideo.objects.create(
            youtube_video_id="aqz-KE-bpKQ",
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_title="Mismatched identity",
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        self.apps = self.executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_idempotency_conflicts_and_selective_reverse(self):
        User = self.apps.get_model("auth", "User")
        ScrapedVideo = self.apps.get_model("scraper", "ScrapedVideo")
        TranscriptEntry = self.apps.get_model("scraper", "TranscriptEntry")
        VideoProject = self.apps.get_model("scraper", "VideoProject")
        SourceVideo = self.apps.get_model("scraper", "SourceVideo")
        TranscriptChunk = self.apps.get_model("scraper", "TranscriptChunk")
        Conflict = self.apps.get_model("scraper", "LegacyImportConflict")

        self.assertEqual(ScrapedVideo.objects.count(), 3)
        self.assertEqual(TranscriptEntry.objects.count(), 2)
        self.assertEqual(SourceVideo.objects.count(), 2)
        self.assertEqual(TranscriptChunk.objects.count(), 2)

        imported_project = VideoProject.objects.get(title="Imported transcript history")
        self.assertEqual(imported_project.owner.username, "__legacy_transcript_import__")
        self.assertFalse(imported_project.owner.is_active)
        self.assertEqual(imported_project.status, "draft")

        complete_source = SourceVideo.objects.get(
            legacy_scraped_video_id=self.complete.pk
        )
        self.assertEqual(complete_source.title, "Unicode — complete")
        self.assertEqual(complete_source.created_at, self.complete_created_at)
        self.assertEqual(
            list(complete_source.transcript_chunks.values_list("sequence", flat=True)),
            [1, 3],
        )
        self.assertEqual(
            complete_source.transcript_chunks.get(sequence=1).created_at,
            self.first_entry_created_at,
        )

        empty_source = SourceVideo.objects.get(legacy_scraped_video_id=self.empty.pk)
        self.assertEqual(empty_source.title, "jwXITbC9pVI")
        self.assertEqual(empty_source.transcript_status, "pending")
        self.assertFalse(
            SourceVideo.objects.filter(legacy_scraped_video_id=self.mismatch.pk).exists()
        )
        self.assertTrue(
            Conflict.objects.filter(
                legacy_scraped_video_id=self.empty.pk,
                severity="warning",
                reason_code="blank_title_fallback",
            ).exists()
        )
        self.assertTrue(
            Conflict.objects.filter(
                legacy_scraped_video_id=self.mismatch.pk,
                severity="error",
                reason_code="invalid_video_identity",
            ).exists()
        )

        migration_module = importlib.import_module(
            "scraper.migrations.0005_import_legacy_history"
        )
        migration_module.import_legacy_history(self.apps, None)
        self.assertEqual(SourceVideo.objects.count(), 2)
        self.assertEqual(TranscriptChunk.objects.count(), 2)
        self.assertEqual(Conflict.objects.count(), 2)

        regular_user = User.objects.create(
            username="unrelated-owner",
            password="!",
            is_active=True,
        )
        unrelated = VideoProject.objects.create(
            owner_id=regular_user.pk,
            title="Unrelated canonical project",
            status="draft",
        )

        executor = MigrationExecutor(connection)
        executor.migrate([("scraper", "0004_restore_canonical_schema")])
        reverse_apps = executor.loader.project_state(
            [("scraper", "0004_restore_canonical_schema")]
        ).apps
        ReverseProject = reverse_apps.get_model("scraper", "VideoProject")
        ReverseSource = reverse_apps.get_model("scraper", "SourceVideo")
        ReverseConflict = reverse_apps.get_model("scraper", "LegacyImportConflict")
        ReverseLegacyVideo = reverse_apps.get_model("scraper", "ScrapedVideo")
        ReverseLegacyEntry = reverse_apps.get_model("scraper", "TranscriptEntry")
        ReverseUser = reverse_apps.get_model("auth", "User")

        self.assertTrue(ReverseProject.objects.filter(pk=unrelated.pk).exists())
        self.assertEqual(ReverseSource.objects.count(), 0)
        self.assertEqual(ReverseConflict.objects.count(), 0)
        self.assertEqual(ReverseLegacyVideo.objects.count(), 3)
        self.assertEqual(ReverseLegacyEntry.objects.count(), 2)
        retained_import_user = ReverseUser.objects.get(
            username="__legacy_transcript_import__"
        )
        self.assertFalse(retained_import_user.is_active)
        self.assertTrue(str(retained_import_user.password).startswith("!"))
