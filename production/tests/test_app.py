from django.apps import apps
from django.test import SimpleTestCase


class ProductionAppConfigTests(SimpleTestCase):
    def test_production_app_is_registered(self):
        app_config = apps.get_app_config("production")

        self.assertEqual(app_config.name, "production")

