"""Project navigation. Read-only database summaries; never probe media or call providers."""
from django.urls import reverse
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


def navigation(project):
    groups = collections(project)
    rows = []
    summaries = {}
    for key, label, help_text, prerequisite in STAGES:
        if key in {"segments", "recording"}:
            summary = summaries["analysis" if key == "segments" else "timeline"]
        elif key == "overview":
            summary = (1, project.get_status_display())
        else:
            # One bounded summary per stage, independent of artifact count.
            from django.db.models import Count
            queryset = groups[key]
            if key == "selected":
                count = queryset.count()
                summary = (count, "In progress" if count else "Not started")
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
                    elif key == "jobs" and count and counts.get("succeeded") == count:
                        status = "Complete"
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
