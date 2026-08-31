import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
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
        form.save()
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
        result = TranscriptService().ingest(project, form.cleaned_data["youtube_url"])
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
        getattr(ProjectWorkflowService, action)(project)
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
        ProjectWorkflowService.ensure_deletable(project)
    except ProjectWorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("project_detail", pk=project.pk)
    title = project.title
    with transaction.atomic():
        project.delete()
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
        ProjectWorkflowService.ensure_deletable(source_video.project)
    except ProjectWorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("transcript_detail", pk=source_video.pk)
    with transaction.atomic():
        project = source_video.project
        source_video.delete()
        if (
            project.status == VideoProject.Status.TRANSCRIPT_READY
            and not project.source_videos.exists()
        ):
            project.status = VideoProject.Status.DRAFT
            project.save(update_fields=["status", "updated_at"])
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
    project_id = payload.get("project_id")
    if not project_id:
        return JsonResponse({"error": "project_id is required."}, status=400)
    project = get_authorized_project(request, project_id)
    try:
        result = TranscriptService().ingest(project, payload.get("url", ""))
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
