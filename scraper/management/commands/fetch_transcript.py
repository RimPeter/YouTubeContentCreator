from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from scraper.models import VideoProject
from scraper.services import (
    DuplicateSourceError,
    ProjectMutationForbiddenError,
    ProjectWorkflowService,
    TranscriptService,
    TranscriptServiceError,
)


class Command(BaseCommand):
    help = "Fetch and save a YouTube transcript through TranscriptService."

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True, help="YouTube URL or video ID")
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--project-id", type=int)
        target.add_argument("--new-project-title")
        parser.add_argument(
            "--owner",
            help="Username that owns a newly created project; required with --new-project-title",
        )

    def _resolve_project(self, options):
        if options["project_id"]:
            try:
                return VideoProject.objects.get(pk=options["project_id"]), False
            except VideoProject.DoesNotExist as exc:
                raise CommandError("The requested project does not exist.") from exc
        if not options["owner"]:
            raise CommandError("--owner is required with --new-project-title.")
        User = get_user_model()
        try:
            owner = User.objects.get(username=options["owner"], is_active=True)
        except User.DoesNotExist as exc:
            raise CommandError("The requested active owner does not exist.") from exc
        return (
            VideoProject.objects.create(
                owner=owner,
                title=options["new_project_title"],
            ),
            True,
        )

    def handle(self, *args, **options):
        project, project_created = self._resolve_project(options)
        try:
            result = TranscriptService().ingest(project, options["url"])
        except DuplicateSourceError as exc:
            self.stdout.write(
                self.style.WARNING(
                    f"Duplicate: source video {exc.source_video.pk} is already attached."
                )
            )
            return
        except (TranscriptServiceError, ProjectMutationForbiddenError) as exc:
            if project_created:
                ProjectWorkflowService.discard_empty_draft(project)
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            if project_created:
                ProjectWorkflowService.discard_empty_draft(project)
            raise CommandError("Transcript ingestion failed.") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f'Saved {result.chunks_created} transcript chunks for "{result.source_video.title}" '
                f"in project {project.pk}."
            )
        )
