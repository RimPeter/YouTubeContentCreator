from django.core.management.base import BaseCommand, CommandError
from django.core.files.storage import default_storage

from production.services.probe import FFprobeClient, MediaProbeError


class Command(BaseCommand):
    help = "Check required production-pipeline tools and configured storage."

    def handle(self, *args, **options):
        self.stdout.write(f"Storage backend: {default_storage.__class__.__name__}")
        try:
            version = FFprobeClient().healthcheck()
        except MediaProbeError as exc:
            raise CommandError(f"FFprobe unavailable ({exc.code}).") from exc
        self.stdout.write(self.style.SUCCESS(version))
        self.stdout.write(self.style.SUCCESS("Production tool health check passed."))
