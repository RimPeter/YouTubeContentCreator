from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from production.services.probe import MediaProbeError


class ProductionHealthCommandTests(SimpleTestCase):
    @patch("production.management.commands.check_production_health.FFprobeClient")
    def test_health_command_reports_success(self, client_class):
        client_class.return_value.healthcheck.return_value = "ffprobe version test"
        output = StringIO()

        call_command("check_production_health", stdout=output)

        self.assertIn("ffprobe version test", output.getvalue())
        self.assertIn("health check passed", output.getvalue())

    @patch("production.management.commands.check_production_health.FFprobeClient")
    def test_health_command_fails_with_sanitized_code(self, client_class):
        client_class.return_value.healthcheck.side_effect = MediaProbeError(
            "ffprobe_unavailable", "sensitive detail"
        )

        with self.assertRaisesMessage(CommandError, "ffprobe_unavailable"):
            call_command("check_production_health", stdout=StringIO())
