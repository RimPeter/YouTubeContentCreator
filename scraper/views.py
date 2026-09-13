import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import TranscriptIngestionForm, VideoProjectForm
from .models import SourceVideo, VideoProject
from .services import (
    DuplicateSourceError,
    InvalidVideoInputError,
    ProjectMutationForbiddenError,
    ProjectWorkflowError,
    ProjectWorkflowService,
    TranscriptRemoteError,
    TranscriptService,
    TranscriptUnavailableError,
)


def project_queryset_for(user):
    queryset = VideoProject.objects.select_related("owner")
    return queryset if user.is_staff or user.is_superuser else queryset.filter(owner=user)


def source_queryset_for(user):
    queryset = SourceVideo.objects.select_related("project", "project__owner")
    return queryset if user.is_staff or user.is_superuser else queryset.filter(project__owner=user)


def get_authorized_project(request, pk):
    return get_object_or_404(project_queryset_for(request.user), pk=pk)


@login_required
def project_list(request):
    projects = project_queryset_for(request.user).prefetch_related("source_videos")
    return render(request, "scraper/project_list.html", {"projects": projects})


@login_required
def project_workflow(request, pk, stage):
    from django.http import Http404
    from django.core.paginator import Paginator
    from .workflow import navigation, collections, artifact_link
    project = get_authorized_project(request, pk)
    rows = navigation(project)
    current = next((row for row in rows if row["key"] == stage), None)
    if current is None:
        raise Http404("Unknown workflow stage.")
    if stage == "overview":
        return redirect("project_detail", pk=pk)
    groups = collections(project)
    page = Paginator(groups[stage], 30).get_page(request.GET.get("page"))
    artifacts = []
    for item in page:
        title = (getattr(item, "title", "") or getattr(item, "source_title", "") or
                 (item.source_video.title if stage in {"analysis", "segments"} else current["label"]))
        artifacts.append({"title": title, "pk": item.pk, "version": getattr(item, "version", None),
                          "status": item.get_status_display() if hasattr(item, "get_status_display") else "",
                          "url": artifact_link(stage, item, project)})
    actions = {
        "sources": ("Add source / manage project", "project_detail"),
        "analysis": ("Run or inspect analysis", "analysis:project_dashboard"),
        "selected": ("Trim and order selected segments", "analysis:project_dashboard"),
        "plan": ("Create or manage reaction plans", "editorial:project_editorial"),
        "clips": ("Upload media and prepare clips", "production:project_clips"),
        "research": ("Manage research and evidence", "editorial:project_editorial"),
        "jobs": ("Inspect processing jobs", "production:project_jobs"),
    }
    from django.urls import reverse
    action = actions.get(stage)
    return render(request, "scraper/workflow_stage.html", {
        "project": project, "workflow_stage": stage, "workflow_navigation": rows, "stage": current,
        "artifacts": artifacts, "page": page,
        "continue": artifacts[0] if page.paginator.count == 1 else None,
        "prerequisite": next(row for row in rows if row["key"] == current["prerequisite"]),
        "action_label": action[0] if action else "", "action_url": reverse(action[1], args=[pk]) if action else "",
    })


@login_required
def project_create(request):
    form = VideoProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        project = form.save(commit=False)
        project.owner = request.user
        project.save()
        messages.success(request, "Project created.")
        return redirect("project_detail", pk=project.pk)
    return render(request, "scraper/project_form.html", {"form": form, "heading": "Create project"})


@login_required
def project_detail(request, pk):
    project = get_authorized_project(request, pk)
    return render(
        request,
        "scraper/project_detail.html",
        {
            "project": project,
            "source_videos": project.source_videos.prefetch_related("transcript_chunks"),
            "ingestion_form": TranscriptIngestionForm(),
        },
    )


@login_required
def project_update(request, pk):
    project = get_authorized_project(request, pk)
    if not project.content_is_mutable:
        messages.error(request, "Approved, locked, or archived project content cannot be edited.")
        return redirect("project_detail", pk=project.pk)
    form = VideoProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        try:
            ProjectWorkflowService.update(project, form.cleaned_data, request.user)
        except ProjectWorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Project updated.")
        return redirect("project_detail", pk=project.pk)
    return render(request, "scraper/project_form.html", {"form": form, "heading": "Edit project"})


@login_required
@require_POST
def project_ingest(request, pk):
    project = get_authorized_project(request, pk)
    form = TranscriptIngestionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a YouTube URL or video ID.")
        return redirect("project_detail", pk=project.pk)
    try:
        result = TranscriptService().ingest(project, form.cleaned_data["youtube_url"], user=request.user)
    except DuplicateSourceError as exc:
        messages.warning(request, str(exc))
    except (InvalidVideoInputError, ProjectMutationForbiddenError) as exc:
        messages.error(request, str(exc))
    except TranscriptUnavailableError:
        messages.error(request, "No transcript is available for that video.")
    except TranscriptRemoteError:
        messages.error(request, "YouTube could not provide the transcript right now.")
    else:
        messages.success(request, f"Saved {result.chunks_created} transcript chunks.")
    return redirect("project_detail", pk=project.pk)


def _run_workflow_action(request, pk, action, success_message):
    project = get_authorized_project(request, pk)
    try:
        getattr(ProjectWorkflowService, action)(project, request.user)
    except ProjectWorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, success_message)
    return redirect("project_detail", pk=project.pk)


@login_required
@require_POST
def project_approve(request, pk):
    return _run_workflow_action(request, pk, "approve", "Project approved.")


@login_required
@require_POST
def project_lock(request, pk):
    return _run_workflow_action(request, pk, "lock", "Project locked.")


@login_required
@require_POST
def project_unlock(request, pk):
    return _run_workflow_action(request, pk, "unlock", "Project unlocked.")


@login_required
@require_POST
def project_archive(request, pk):
    return _run_workflow_action(request, pk, "archive", "Project archived.")


@login_required
@require_POST
def project_delete(request, pk):
    project = get_authorized_project(request, pk)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Deletion requires explicit confirmation.")
        return redirect("project_detail", pk=project.pk)
    try:
        ProjectWorkflowService.delete(project, request.user)
    except ProjectWorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("project_detail", pk=project.pk)
    title = project.title
    messages.success(request, f'Project "{title}" deleted.')
    return redirect("project_list")


@login_required
def source_video_list(request):
    source_videos = source_queryset_for(request.user).order_by("-created_at")
    return render(request, "scraper/scraped_video_list.html", {"source_videos": source_videos})


@login_required
def transcript_detail(request, pk):
    source_video = get_object_or_404(source_queryset_for(request.user), pk=pk)
    return render(
        request,
        "scraper/transcript_detail.html",
        {
            "source_video": source_video,
            "chunks": source_video.transcript_chunks.order_by("sequence"),
        },
    )


@login_required
@require_POST
def delete_transcript(request, pk):
    source_video = get_object_or_404(source_queryset_for(request.user), pk=pk)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Deletion requires explicit confirmation.")
        return redirect("transcript_detail", pk=source_video.pk)
    try:
        ProjectWorkflowService.delete_source(source_video, request.user)
    except ProjectWorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("transcript_detail", pk=source_video.pk)
    messages.success(request, "Saved transcript deleted.")
    return redirect("source_video_list")


@login_required
def scraper_form(request):
    return redirect("project_list")


@login_required
def scraped_video_list(request):
    return source_video_list(request)


@login_required
@require_POST
def fetch_transcript_api(request):
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "JSON body must be an object."}, status=400)
    project_id = payload.get("project_id")
    if type(project_id) is not int or not 1 <= project_id <= 9223372036854775807:
        return JsonResponse({"error": "project_id must be a positive integer."}, status=400)
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip() or len(url) > 500:
        return JsonResponse({"error": "url must be a non-empty string of at most 500 characters."}, status=400)
    project = get_authorized_project(request, project_id)
    try:
        result = TranscriptService().ingest(project, url, user=request.user)
    except InvalidVideoInputError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except DuplicateSourceError as exc:
        return JsonResponse(
            {"error": str(exc), "source_video_id": exc.source_video.pk},
            status=409,
        )
    except ProjectMutationForbiddenError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except TranscriptUnavailableError:
        return JsonResponse({"error": "No transcript is available for that video."}, status=422)
    except TranscriptRemoteError:
        return JsonResponse({"error": "YouTube transcript retrieval failed."}, status=502)
    return JsonResponse(
        {
            "source_video_id": result.source_video.pk,
            "chunks_created": result.chunks_created,
        },
        status=201,
    )
