from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class EditorialInitialMigrationTests(TransactionTestCase):
    migrate_to = ("editorial", "0001_initial")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([("editorial", None)])
        prior_apps = executor.loader.project_state(
            [("production", "0003_sourceclip")]
        ).apps
        User = prior_apps.get_model("auth", "User")
        VideoProject = prior_apps.get_model("scraper", "VideoProject")
        user = User.objects.create(username="migration-editorial-owner", password="!")
        self.project_pk = VideoProject.objects.create(owner_id=user.pk, title="Editorial migration").pk

    def tearDown(self):
        MigrationExecutor(connection).migrate([self.migrate_to])
        super().tearDown()

    def test_additive_forward_and_reverse_preserve_project_data(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        VideoProject = apps.get_model("scraper", "VideoProject")
        self.assertTrue(VideoProject.objects.filter(pk=self.project_pk).exists())
        for model_name in ("ResearchPackage", "EvidenceSource", "ReactionBlock", "ReactionClaim"):
            self.assertEqual(apps.get_model("editorial", model_name).objects.count(), 0)

        executor = MigrationExecutor(connection)
        executor.migrate([("editorial", None)])
        reverse_apps = executor.loader.project_state(
            [("production", "0003_sourceclip")]
        ).apps
        ReverseProject = reverse_apps.get_model("scraper", "VideoProject")
        self.assertTrue(ReverseProject.objects.filter(pk=self.project_pk).exists())
