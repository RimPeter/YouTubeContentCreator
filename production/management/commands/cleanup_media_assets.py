from django.core.management.base import BaseCommand, CommandError

from production.services.access import ProductionValidationError
from production.services.cleanup import MediaCleanupService


class Command(BaseCommand):
    help = "List or remove old failed/superseded, unreferenced media assets."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-days", type=int, required=True)
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Delete eligible rows and files. The default is a dry run.",
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        try:
            candidates = MediaCleanupService.cleanup(
                options["older_than_days"], execute=execute
            )
        except ProductionValidationError as exc:
            raise CommandError(str(exc)) from exc
        mode = "deleted" if execute else "would delete"
        for asset_id, stored_name in candidates:
            self.stdout.write(f"{mode} asset {asset_id}: {stored_name or '[no file]'}")
        if not execute:
            self.stdout.write("Dry run only. Pass --execute to delete eligible assets.")
        self.stdout.write(f"{len(candidates)} eligible asset(s).")
