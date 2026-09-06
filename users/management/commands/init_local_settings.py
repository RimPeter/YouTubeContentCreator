import os
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create a private, persistent development secret key without replacing an existing key."

    def handle(self, *args, **options):
        if settings.PRODUCTION:
            raise CommandError("Production must use DJANGO_SECRET_KEY from the environment.")
        path = settings.LOCAL_SECRET_KEY_FILE
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self.stdout.write("Local secret key already exists; preserved it.")
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(64) + "\n")
        self.stdout.write(self.style.SUCCESS("Created a local development secret key. Restart any running server."))
