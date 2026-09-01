from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ProductionInitialMigrationTests(TransactionTestCase):
    migrate_to = ("production", "0003_sourceclip")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([("production", None)])
        prior_apps = executor.loader.project_state(
            [("scraper", "0005_import_legacy_history")]
        ).apps
        User = prior_apps.get_model("auth", "User")
        VideoProject = prior_apps.get_model("scraper", "VideoProject")
        user = User.objects.create(username="migration-production-owner", password="!")
        project = VideoProject.objects.create(owner_id=user.pk, title="Production migration")
        self.project_pk = project.pk

    def tearDown(self):
        MigrationExecutor(connection).migrate([self.migrate_to])
        super().tearDown()

    def test_additive_forward_and_reverse_preserve_project_data(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        VideoProject = apps.get_model("scraper", "VideoProject")
        MediaAsset = apps.get_model("production", "MediaAsset")
        PipelineJob = apps.get_model("production", "PipelineJob")
        ArtifactDependency = apps.get_model("production", "ArtifactDependency")
        SourceClip = apps.get_model("production", "SourceClip")

        self.assertTrue(VideoProject.objects.filter(pk=self.project_pk).exists())
        self.assertEqual(MediaAsset.objects.count(), 0)
        self.assertEqual(PipelineJob.objects.count(), 0)
        self.assertEqual(ArtifactDependency.objects.count(), 0)
        self.assertEqual(SourceClip.objects.count(), 0)

        executor = MigrationExecutor(connection)
        executor.migrate([("production", None)])
        reverse_apps = executor.loader.project_state(
            [("scraper", "0005_import_legacy_history")]
        ).apps
        ReverseProject = reverse_apps.get_model("scraper", "VideoProject")
        self.assertTrue(ReverseProject.objects.filter(pk=self.project_pk).exists())
