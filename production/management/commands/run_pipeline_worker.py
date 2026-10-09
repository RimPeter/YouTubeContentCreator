import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

from production.services.clips import SourceClipError, SourceClipService
from production.services.jobs import PipelineJobService
from editorial.services.ai_research import AIResearchService
from editorial.services.ai_reactions import AIReactionService
from editorial.services.access import EditorialServiceError
from analysis.services.jobs import AnalysisJobService
from analysis.services.analysis import AnalysisServiceError
from editorial.services.reaction_sequence_drafts import ReactionSequenceDraftService


class Command(BaseCommand):
    help = "Process queued clips, AI research and scripts, and recover expired attempts. Run as a supervised service."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Recover expired jobs and attempt one queued job.")
        parser.add_argument("--poll-interval", type=float, default=2)
        parser.add_argument("--max-jobs", type=int, default=0, help="Exit after this many attempts (0 means continuous).")

    def handle(self, *args, **options):
        if not 0.1 <= options["poll_interval"] <= 60 or options["max_jobs"] < 0:
            raise CommandError("Poll interval must be 0.1–60 seconds and max-jobs non-negative.")
        processed = 0
        try:
            while True:
                close_old_connections()
                try:
                    recovered = PipelineJobService.recover_expired()
                    if recovered:
                        self.stdout.write(f"Recovered {recovered} interrupted attempt(s).")
                    job = PipelineJobService.claim_next()
                    if job:
                        try:
                            with PipelineJobService.maintain_lease(job):
                                if job.job_type == "ai_research":
                                    package = AIResearchService.process_job(job)
                                    result = f"research {package.pk} ready for review"
                                elif job.job_type == "ai_reaction":
                                    block = AIReactionService.process_job(job)
                                    result = f"reaction {block.pk} ready for review"
                                elif job.job_type == "transcript_analysis":
                                    run = AnalysisJobService.process_job(job)
                                    result = f"analysis {run.pk} ready for review"
                                elif job.job_type == "sequence_reaction":
                                    draft = ReactionSequenceDraftService.process_job(job)
                                    result = f"continuous script {draft.pk} ready for review"
                                else:
                                    clip = SourceClipService().process_job(job)
                                    result = f"clip {clip.pk} ready for review"
                            self.stdout.write(f"Job {job.pk}: {result}.")
                        except (SourceClipError, EditorialServiceError, AnalysisServiceError) as exc:
                            self.stderr.write(f"Job {job.pk}: {exc}")
                        processed += 1
                except OperationalError:
                    # Another process may briefly hold SQLite's write lock.
                    # Never claim work without the database transaction succeeding.
                    if options["once"]:
                        raise CommandError("Database is busy; retry the worker command.")
                    job = None
                if options["once"] or (options["max_jobs"] and processed >= options["max_jobs"]):
                    break
                if not job:
                    time.sleep(options["poll_interval"])
        except KeyboardInterrupt:
            self.stdout.write("Worker stopped; any unfinished lease will be recovered on restart.")
