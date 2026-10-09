"""Project navigation. Read-only database summaries; never probe media or call providers."""
from django.urls import reverse
from django.db.models import Count, Exists, OuterRef
from analysis.models import AnalysisRun, SegmentSelection
from editorial.models import ReactionSequencePlan, ReactionSequenceDraft, ReactionTimeline, ReactionProductionAssembly, ResearchPackage
from production.models import SourceClip, PipelineJob


STAGES = [
    ("overview", "Overview", "Review project details and approve complete transcripts.", "sources"),
    ("sources", "Sources & transcripts", "Add source transcripts from the project overview.", "overview"),
    ("analysis", "Analysis", "Approve the project, then run analysis on a source transcript.", "sources"),
    ("segments", "Segment review", "Run analysis to inspect topic boundaries, scores and recommendations.", "analysis"),
    ("selected", "Selected segments", "Select segments, then trim and arrange their order.", "segments"),
    ("plan", "Reaction plan", "Select at least one segment to create a reaction plan.", "selected"),
    ("script", "Reaction script", "Review a reaction plan and mark it ready before generating a script.", "plan"),
    ("timeline", "Reaction timeline", "Generate a reaction script, then create its timeline.", "script"),
    ("clips", "Source clips", "Prepare approved source clips for your selected segments.", "selected"),
    ("research", "Research & evidence", "Use approved clips to collect and review supporting evidence.", "clips"),
    ("recording", "Recording review", "Create a timeline, review the script, then upload and select narration.", "timeline"),
    ("handoff", "Production handoff", "Complete recording review and resolve missing media before creating an assembly.", "recording"),
    ("jobs", "Jobs & activity", "Monitor processing and inspect failed jobs.", "overview"),
]


def collections(project):
    runs = AnalysisRun.objects.filter(source_video__project=project).select_related("source_video").order_by("-started_at", "-pk")
    return {
        "sources": project.source_videos.all(),
        "analysis": runs, "segments": runs,
        "selected": SegmentSelection.objects.active().filter(project=project).select_related("analysis_segment").order_by("order"),
        "plan": ReactionSequencePlan.objects.filter(project=project).order_by("-created_at"),
        "script": ReactionSequenceDraft.objects.filter(project=project).order_by("-created_at"),
        "timeline": ReactionTimeline.objects.filter(project=project).order_by("-created_at"),
        "clips": SourceClip.objects.filter(project=project).order_by("-created_at"),
        "research": ResearchPackage.objects.filter(project=project).order_by("-created_at"),
        "recording": ReactionTimeline.objects.filter(project=project).order_by("-created_at"),
        "handoff": ReactionProductionAssembly.objects.filter(timeline__project=project).order_by("-created_at"),
        "jobs": PipelineJob.objects.filter(project=project).order_by("-created_at"),
    }


def latest_versions(queryset, parent_field):
    newer = queryset.model.objects.filter(
        **{parent_field: OuterRef(parent_field)}, version__gt=OuterRef("version"))
    return queryset.filter(~Exists(newer))


def current_collections(project):
    """Actionable versions only; collections() deliberately retains full history."""
    groups = collections(project)
    groups["analysis"] = latest_versions(groups["analysis"], "source_video_id")
    groups["segments"] = groups["analysis"]
    groups["plan"] = latest_versions(groups["plan"], "project_id")
    groups["script"] = latest_versions(groups["script"].filter(plan__in=groups["plan"]), "plan_id")
    groups["timeline"] = latest_versions(groups["timeline"].filter(reaction_draft__in=groups["script"]), "reaction_draft_id")
    groups["recording"] = groups["timeline"]
    groups["handoff"] = latest_versions(groups["handoff"].filter(timeline__in=groups["timeline"]), "timeline_id")
    groups["clips"] = latest_versions(groups["clips"].filter(selected_segment__retired_at__isnull=True), "selected_segment_id")
    groups["research"] = latest_versions(groups["research"].filter(
        source_clip__in=groups["clips"].filter(status="approved")), "source_clip_id")
    newer_jobs = PipelineJob.objects.filter(project=project, job_type=OuterRef("job_type"),
                                            idempotency_key=OuterRef("idempotency_key"), pk__gt=OuterRef("pk"))
    groups["jobs"] = groups["jobs"].filter(~Exists(newer_jobs))
    return groups


COMPLETE_STATUSES = {
    "analysis": {"succeeded"}, "plan": {"ready"}, "clips": {"approved"},
    "research": {"ready"}, "timeline": {"recording", "ready"},
    "recording": {"ready"}, "handoff": {"ready"}, "jobs": {"succeeded", "cancelled"},
}


def navigation(project):
    groups = current_collections(project)
    rows = []
    summaries = {}
    for key, label, help_text, prerequisite in STAGES:
        if key == "segments":
            runs = groups["analysis"]
            from analysis.models import AnalysisSegment, AnalysisSegmentReview
            segments = AnalysisSegment.objects.filter(analysis_run__in=runs)
            reviews = AnalysisSegmentReview.objects.filter(analysis_segment_id=OuterRef("pk"))
            total = segments.count()
            pending = segments.filter(~Exists(reviews)).exists()
            summary = (total, "Needs review" if pending else "Complete" if total else "Not started")
        elif key == "overview":
            summary = (1, project.get_status_display())
        else:
            # One bounded summary per stage, independent of artifact count.
            queryset = groups[key]
            if key == "selected":
                count = queryset.count()
                summary = (count, "Complete" if count else "Not started")
            else:
                field = "transcript_status" if key == "sources" else "status"
                counts = dict(queryset.order_by().values_list(field).annotate(total=Count("pk")))
                count = sum(counts.values())
                status = ("Needs update" if counts.get("stale") or counts.get("failed")
                          else "Needs review" if count else "Not started")
                if not counts.get("stale") and not counts.get("failed"):
                    if counts.get("running") or counts.get("queued") or counts.get("processing"):
                        status = "In progress"
                    elif key == "sources" and count and counts.get("completed") == count and project.status == "approved":
                        status = "Complete"
                    elif count and set(counts) <= COMPLETE_STATUSES.get(key, set()):
                        status = "Complete"
                if key == "clips" and count < groups["selected"].count():
                    status = "Needs update" if count else "Not started"
                if key == "research" and count < groups["clips"].filter(status="approved").count():
                    status = "Needs update" if count else "Not started"
                if key == "script" and count and groups["timeline"].exclude(status="stale").exists() and not counts.get("stale") and not counts.get("failed"):
                    status = "Complete"
                job_type = {"analysis": "transcript_analysis", "script": "sequence_reaction"}.get(key)
                if job_type and groups["jobs"].filter(job_type=job_type, status__in=["queued", "running"]).exists():
                    status = "In progress"
                summary = (count, status)
        summaries[key] = summary
        rows.append({"key": key, "label": label, "help": help_text, "prerequisite": prerequisite,
                     "count": summary[0], "status": summary[1],
                     "url": reverse("project_workflow", args=[project.pk, key])})
    return rows


def artifact_link(stage, item, project):
    routes = {"sources": "transcript_detail", "analysis": "analysis:run_detail", "segments": "analysis:run_detail",
              "plan": "editorial:reaction_plan_detail", "script": "editorial:reaction_sequence_draft_detail",
              "timeline": "editorial:reaction_timeline_detail", "recording": "editorial:recording_review",
              "clips": "production:clip_detail", "research": "editorial:research_detail",
              "handoff": "editorial:reaction_assembly_detail"}
    if stage in {"selected", "jobs"}:
        return reverse("analysis:project_dashboard" if stage == "selected" else "production:project_jobs", args=[project.pk])
    return reverse(routes[stage], args=[item.pk])
