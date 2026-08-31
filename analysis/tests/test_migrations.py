from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AnalysisInitialMigrationTests(TransactionTestCase):
    migrate_to = ("analysis", "0002_add_boundary_constraints")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([("analysis", None)])
        scraper_apps = executor.loader.project_state(
            [("scraper", "0005_import_legacy_history")]
        ).apps
        User = scraper_apps.get_model("auth", "User")
        VideoProject = scraper_apps.get_model("scraper", "VideoProject")
        SourceVideo = scraper_apps.get_model("scraper", "SourceVideo")
        TranscriptChunk = scraper_apps.get_model("scraper", "TranscriptChunk")
        user = User.objects.create(username="migration-analysis-owner", password="!")
        project = VideoProject.objects.create(owner_id=user.pk, title="Migration project")
        source = SourceVideo.objects.create(
            project_id=project.pk,
            youtube_url="https://youtu.be/dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="Migration source",
            transcript_status="completed",
        )
        TranscriptChunk.objects.create(
            source_video_id=source.pk,
            sequence=1,
            start_seconds=0,
            duration_seconds=1,
            text="Migration chunk",
        )
        self.project_pk = project.pk
        self.source_pk = source.pk

    def tearDown(self):
        MigrationExecutor(connection).migrate([self.migrate_to])
        super().tearDown()

    def test_additive_forward_and_reverse_preserve_scraper_data(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        VideoProject = apps.get_model("scraper", "VideoProject")
        SourceVideo = apps.get_model("scraper", "SourceVideo")
        TranscriptChunk = apps.get_model("scraper", "TranscriptChunk")
        AnalysisRun = apps.get_model("analysis", "AnalysisRun")
        self.assertTrue(VideoProject.objects.filter(pk=self.project_pk).exists())
        self.assertTrue(SourceVideo.objects.filter(pk=self.source_pk).exists())
        self.assertEqual(TranscriptChunk.objects.filter(source_video_id=self.source_pk).count(), 1)
        self.assertEqual(AnalysisRun.objects.count(), 0)

        executor = MigrationExecutor(connection)
        executor.migrate([("analysis", None)])
        reverse_apps = executor.loader.project_state(
            [("scraper", "0005_import_legacy_history")]
        ).apps
        ReverseProject = reverse_apps.get_model("scraper", "VideoProject")
        ReverseSource = reverse_apps.get_model("scraper", "SourceVideo")
        ReverseChunk = reverse_apps.get_model("scraper", "TranscriptChunk")
        self.assertTrue(ReverseProject.objects.filter(pk=self.project_pk).exists())
        self.assertTrue(ReverseSource.objects.filter(pk=self.source_pk).exists())
        self.assertEqual(ReverseChunk.objects.filter(source_video_id=self.source_pk).count(), 1)
