from django import template
from scraper.models import VideoProject
from scraper.workflow import navigation

register = template.Library()


@register.simple_tag(takes_context=True)
def project_navigation(context):
    request = context.get("request")
    if not request or not request.user.is_authenticated:
        return None
    project, artifact, stage = context.get("project"), None, context.get("workflow_stage", "overview")
    for key, candidate_stage in [("assembly", "handoff"), ("timeline", "timeline"), ("draft", "script"),
                                 ("plan", "plan"), ("run", "segments"), ("source_video", "sources"),
                                 ("clip", "clips"), ("package", "research"), ("reaction", "script")]:
        value = context.get(key)
        if value is not None:
            artifact, stage = value, candidate_stage
            if key == "assembly":
                project = value.timeline.project
            elif key == "run":
                project = value.source_video.project
            else:
                project = value.project
            break
    if project is None:
        form = context.get("form")
        if form and isinstance(getattr(form, "instance", None), VideoProject) and form.instance.pk:
            project = form.instance
    if not isinstance(project, VideoProject):
        return None
    if not (request.user.is_staff or request.user.is_superuser or project.owner_id == request.user.pk):
        return None
    name = request.resolver_match.view_name
    stage = {"analysis:project_dashboard": "analysis", "production:project_clips": "clips",
             "production:project_jobs": "jobs", "editorial:project_editorial": "research",
             "editorial:recording_review": "recording"}.get(name, stage)
    rows = context.get("workflow_navigation") or navigation(project)
    for row in rows:
        row["active"] = row["key"] == stage
        # Paired stages keep the exact timeline rather than selecting another version.
        if context.get("timeline") and row["key"] in {"timeline", "recording"}:
            from django.urls import reverse
            row["url"] = reverse("editorial:recording_review" if row["key"] == "recording" else "editorial:reaction_timeline_detail",
                                 args=[context["timeline"].pk])
        if context.get("draft") and row["key"] == "plan":
            from django.urls import reverse
            row["url"] = reverse("editorial:reaction_plan_detail", args=[context["draft"].plan_id])
        if context.get("timeline") and row["key"] == "script":
            from django.urls import reverse
            row["url"] = reverse("editorial:reaction_sequence_draft_detail", args=[context["timeline"].reaction_draft_id])
        if context.get("run") and row["key"] == "sources":
            from django.urls import reverse
            row["url"] = reverse("transcript_detail", args=[context["run"].source_video_id])
    current = next((row for row in rows if row["active"]), rows[0])
    if context.get("is_stale"):
        current["status"] = "Needs update"
    if name == "editorial:recording_review":
        current["status"] = "Needs review" if context.get("issues") else "Complete"
    next_step = next((row for row in rows if row["key"] not in {"overview", "research", "jobs"} and row["status"] == "Needs update"), None)
    if next_step is None:
        next_step = next((row for row in rows if row["key"] not in {"overview", "research", "jobs"} and row["count"] == 0), rows[10])
    return {"project": project, "rows": rows, "current": current,
            "next_step": next_step,
            "artifact": f"#{artifact.pk}" + (f" · version {artifact.version}" if hasattr(artifact, "version") else "") if artifact else ""}
