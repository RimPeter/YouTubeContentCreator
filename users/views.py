from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Exists, OuterRef
from django.shortcuts import render
from django.urls import reverse

from analysis.models import AnalysisRun, SegmentSelection
from editorial.models import ReactionBlock, ResearchPackage
from production.models import MediaAsset, PipelineJob, SourceClip
from scraper.models import SourceVideo, VideoProject


def _latest_versions(queryset, parent_field):
    newer = queryset.model.objects.filter(
        **{parent_field: OuterRef(parent_field)}, version__gt=OuterRef("version")
    )
    return queryset.filter(~Exists(newer))


def _status_counts(queryset, project_field="project_id", status_field="status"):
    result = defaultdict(lambda: defaultdict(int))
    for row in queryset.order_by().values(project_field, status_field).annotate(total=Count("pk")):
        result[row[project_field]][row[status_field]] = row["total"]
    return result


def _next_action(project, counts):
    """Pick a useful entry point; every stage remains available on the project page."""
    if project.status == VideoProject.Status.ARCHIVED:
        return ("View archived project", "project_detail")
    if project.is_locked:
        return ("Review project lock", "project_detail")
    if project.status == VideoProject.Status.TRANSCRIPT_READY:
        return ("Review transcripts", "project_detail")
    if project.status != VideoProject.Status.APPROVED:
        return ("Add a transcript", "project_detail")
    if counts["jobs"].get("running", 0) or counts["jobs"].get("queued", 0):
        return ("View processing progress", "production:project_jobs")
    if counts["clips"].get("validated", 0):
        return ("Review clips", "production:project_clips")
    if counts["selections"] == 0:
        return ("Analyze and select segments", "analysis:project_dashboard")
    if counts["clips"].get("stale", 0) or counts["clips"].get("failed", 0):
        return ("Regenerate clips", "production:project_clips")
    if counts["selections"] > counts["clips"].get("approved", 0):
        if counts["source_media"] == 0:
            return ("Upload source media", "production:project_clips")
        return ("Create clips", "production:project_clips")
    if counts["research"].get("draft", 0):
        return ("Review research", "editorial:project_editorial")
    if counts["reactions"].get("draft", 0):
        return ("Review reactions", "editorial:project_editorial")
    if (
        counts["research"].get("stale", 0)
        or counts["reactions"].get("stale", 0)
        or counts["reactions"].get("failed", 0)
    ):
        return ("Regenerate editorial work", "editorial:project_editorial")
    if counts["clips"].get("approved", 0) > counts["research"].get("ready", 0):
        return ("Create research", "editorial:project_editorial")
    if counts["research"].get("ready", 0) > counts["reactions"].get("approved", 0):
        return ("Draft reactions", "editorial:project_editorial")
    if counts["reactions"].get("approved", 0):
        return ("View approved reactions", "editorial:project_editorial")
    return ("Open project", "project_detail")


def dashboard(request):
    if not request.user.is_authenticated:
        return render(request, "users/dashboard.html")
    projects = VideoProject.objects.all()
    if not (request.user.is_staff or request.user.is_superuser):
        projects = projects.filter(owner=request.user)
    active = projects.exclude(status=VideoProject.Status.ARCHIVED)
    project_ids = active.values("pk")
    sources = _status_counts(
        SourceVideo.objects.filter(project_id__in=project_ids), status_field="transcript_status"
    )
    analyses = _status_counts(
        _latest_versions(AnalysisRun.objects.filter(source_video__project_id__in=project_ids), "source_video_id"),
        project_field="source_video__project_id",
    )
    selections = dict(
        SegmentSelection.objects.filter(project_id__in=project_ids, retired_at__isnull=True)
        .order_by().values("project_id").annotate(total=Count("pk")).values_list("project_id", "total")
    )
    clips = _status_counts(_latest_versions(
        SourceClip.objects.filter(project_id__in=project_ids, selected_segment__retired_at__isnull=True),
        "selected_segment_id",
    ))
    # Only work attached to a current approved clip is actionable in editorial.
    current_clip_ids = _latest_versions(
        SourceClip.objects.filter(project_id__in=project_ids, selected_segment__retired_at__isnull=True),
        "selected_segment_id",
    ).filter(status=SourceClip.Status.APPROVED).values("pk")
    research = _status_counts(_latest_versions(
        ResearchPackage.objects.filter(source_clip_id__in=current_clip_ids), "source_clip_id"
    ))
    reactions = _status_counts(_latest_versions(
        ReactionBlock.objects.filter(source_clip_id__in=current_clip_ids), "source_clip_id"
    ))
    jobs = _status_counts(PipelineJob.objects.filter(project_id__in=project_ids))
    source_media = dict(
        MediaAsset.objects.filter(
            project_id__in=project_ids, kind=MediaAsset.Kind.SOURCE_UPLOAD,
            status__in=[MediaAsset.Status.VALIDATED, MediaAsset.Status.APPROVED], has_video=True,
            duration_seconds__isnull=False, consent_metadata__rights_confirmed=True,
        ).exclude(rights_basis=MediaAsset.RightsBasis.UNKNOWN)
        .order_by().values("project_id").annotate(total=Count("pk")).values_list("project_id", "total")
    )
    summary = {"active_projects": active.count(), "pending_reviews": 0, "failed_jobs": 0, "processing_jobs": 0}
    rows = []
    for project in active.select_related("owner").order_by("-updated_at", "-pk"):
        pk = project.pk
        counts = {
            "sources": sources[pk], "analyses": analyses[pk], "selections": selections.get(pk, 0),
            "clips": clips[pk], "research": research[pk], "reactions": reactions[pk], "jobs": jobs[pk],
            "source_media": source_media.get(pk, 0),
        }
        pending = (
            int(project.status == VideoProject.Status.TRANSCRIPT_READY)
            + clips[pk].get("validated", 0) + research[pk].get("draft", 0) + reactions[pk].get("draft", 0)
        )
        processing = jobs[pk].get("running", 0) + jobs[pk].get("queued", 0)
        stale = clips[pk].get("stale", 0) + research[pk].get("stale", 0) + reactions[pk].get("stale", 0)
        label, route = _next_action(project, counts)
        rows.append({
            "project": project, "pending": pending, "processing": processing, "stale": stale,
            "failed": jobs[pk].get("failed", 0), "analysis_failed": analyses[pk].get("failed", 0),
            "source_count": sum(sources[pk].values()), "selection_count": counts["selections"],
            "approved_reactions": reactions[pk].get("approved", 0),
            "next_label": label, "next_url": reverse(route, args=[pk]),
        })
        summary["pending_reviews"] += pending
        summary["failed_jobs"] += jobs[pk].get("failed", 0)
        summary["processing_jobs"] += processing
    return render(request, "users/dashboard.html", {
        "project_rows": rows[:12], "summary": summary,
        "archived_count": projects.filter(status=VideoProject.Status.ARCHIVED).count(),
        "staff_scope": request.user.is_staff or request.user.is_superuser,
    })


@login_required(login_url='account_login')
def profile(request):
    return render(request, 'users/profile.html')
