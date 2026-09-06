from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


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
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
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


class SelectionRetirementMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        previous = ("analysis", "0002_add_boundary_constraints")
        executor.migrate([previous])
        apps = executor.loader.project_state([previous]).apps
        User = apps.get_model("auth", "User")
        Project = apps.get_model("scraper", "VideoProject")
        Source = apps.get_model("scraper", "SourceVideo")
        Chunk = apps.get_model("scraper", "TranscriptChunk")
        Run = apps.get_model("analysis", "AnalysisRun")
        Segment = apps.get_model("analysis", "AnalysisSegment")
        Selection = apps.get_model("analysis", "SegmentSelection")
        owner = User.objects.create(username="retirement-migration-owner")
        project = Project.objects.create(owner=owner, title="Existing project")
        source = Source.objects.create(project=project, title="Source", youtube_video_id="dQw4w9WgXcQ", youtube_url="https://youtu.be/dQw4w9WgXcQ")
        chunk = Chunk.objects.create(source_video=source, sequence=1, start_seconds=0, duration_seconds=1, text="Existing transcript")
        run = Run.objects.create(source_video=source, version=1, source_fingerprint="a" * 64, algorithm_version="test-v1")
        segment = Segment.objects.create(analysis_run=run, order=1, start_chunk=chunk, end_chunk=chunk, start_seconds=0, end_seconds=1, title="Segment", summary="Summary", source_text="Existing transcript", aggregate_score=50, rationale="Test")
        self.selection_id = Selection.objects.create(project=project, analysis_segment=segment, order=1, notes="Keep history", selected_by=owner).pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_selection_remains_active_and_retirement_permits_new_version(self):
        target = ("analysis", "0003_retire_selections")
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        apps = executor.loader.project_state([target]).apps
        Selection = apps.get_model("analysis", "SegmentSelection")
        existing = Selection.objects.get(pk=self.selection_id)
        self.assertIsNone(existing.retired_at)
        self.assertIsNone(existing.retired_by_id)
        self.assertEqual(existing.notes, "Keep history")
        values = {"project_id": existing.project_id, "analysis_segment_id": existing.analysis_segment_id, "order": 1, "selected_by_id": existing.selected_by_id}
        with self.assertRaises(IntegrityError), transaction.atomic():
            Selection.objects.create(**values)
        existing.retired_at = timezone.now()
        existing.retired_by_id = existing.selected_by_id
        existing.save(update_fields=["retired_at", "retired_by"])
        fresh = Selection.objects.create(**values)
        self.assertNotEqual(fresh.pk, existing.pk)
        self.assertEqual(Selection.objects.count(), 2)
        self.assertEqual(Selection.objects.filter(retired_at__isnull=True).count(), 1)
