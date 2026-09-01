from django.core.management.base import BaseCommand, CommandError
from django.core.files.storage import default_storage

from production.services.probe import FFprobeClient, MediaProbeError
from production.services.ffmpeg import FFmpegClient, FFmpegError


class Command(BaseCommand):
    help = "Check required production-pipeline tools and configured storage."

    def handle(self, *args, **options):
        self.stdout.write(f"Storage backend: {default_storage.__class__.__name__}")
        try:
            ffmpeg_version = FFmpegClient().healthcheck()
            version = FFprobeClient().healthcheck()
        except (FFmpegError, MediaProbeError) as exc:
            raise CommandError(f"Production media tool unavailable ({exc.code}).") from exc
        self.stdout.write(self.style.SUCCESS(ffmpeg_version))
        self.stdout.write(self.style.SUCCESS(version))
        self.stdout.write(self.style.SUCCESS("Production tool health check passed."))
