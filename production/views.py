import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from analysis.models import SegmentSelection
from scraper.models import VideoProject

from .forms import SourceClipCreationForm, SourceMediaUploadForm
from .models import MediaAsset, NarrationTake, PipelineJob, SourceClip
from .services.jobs import PipelineJobService
from .services.access import ProductionServiceError
from .services.clips import SourceClipService
from .services.media_assets import MediaAssetService


def project_queryset_for(user):
    queryset = VideoProject.objects.select_related("owner")
    return queryset if user.is_staff or user.is_superuser else queryset.filter(owner=user)


def selection_queryset_for(user):
    queryset = SegmentSelection.objects.active().select_related(
        "project",
        "analysis_segment__analysis_run__source_video",
    )
    return queryset if user.is_staff or user.is_superuser else queryset.filter(
        project__owner=user
    )


def clip_queryset_for(user):
    queryset = SourceClip.objects.select_related(
        "project",
        "project__owner",
        "selected_segment__analysis_segment__analysis_run__source_video",
        "source_asset",
        "processed_asset",
        "pipeline_job",
        "created_by",
        "approved_by",
    )
    return queryset if user.is_staff or user.is_superuser else queryset.filter(
        project__owner=user
    )


@login_required
def project_clips(request, project_pk):
    project = get_object_or_404(project_queryset_for(request.user), pk=project_pk)
    selections = list(
        project.segment_selections.active().select_related(
            "analysis_segment__analysis_run__source_video"
        ).order_by("order")
    )
    rows = [
        {
            "selection": selection,
            "form": SourceClipCreationForm(project=project),
            "clips": list(
                selection.source_clips.select_related(
                    "source_asset", "processed_asset", "pipeline_job"
                ).order_by("-version")
            ),
        }
        for selection in selections
    ]
    return render(
        request,
        "production/project_clips.html",
        {
            "project": project,
            "rows": rows,
            "source_assets": project.media_assets.filter(
                kind=MediaAsset.Kind.SOURCE_UPLOAD
            ).order_by("display_name", "-version"),
            "upload_form": SourceMediaUploadForm(),
            "retired_selections": project.segment_selections.filter(retired_at__isnull=False)
                .select_related("analysis_segment").prefetch_related("source_clips").order_by("-retired_at"),
        },
    )


@login_required
@require_POST
def upload_source_media(request, project_pk):
    project = get_object_or_404(project_queryset_for(request.user), pk=project_pk)
    form = SourceMediaUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Choose a valid media file and confirm its usage rights.")
        return redirect("production:project_clips", project_pk=project.pk)
    try:
        asset = MediaAssetService().create_upload(
            project,
            form.cleaned_data["media_file"],
            request.user,
            kind=MediaAsset.Kind.SOURCE_UPLOAD,
            rights_basis=form.cleaned_data["rights_basis"],
            rights_notes=form.cleaned_data["rights_notes"],
            consent_metadata={"rights_confirmed": True},
            configuration={"purpose": "source_clip_input"},
        )
    except ProductionServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'Validated source media "{asset.display_name}".')
    return redirect("production:project_clips", project_pk=project.pk)


@login_required
@require_POST
def create_source_clip(request, selection_pk):
    selection = get_object_or_404(selection_queryset_for(request.user), pk=selection_pk)
    form = SourceClipCreationForm(request.POST, project=selection.project)
    if not form.is_valid():
        messages.error(request, "Choose valid source media and padding values.")
        return redirect("production:project_clips", project_pk=selection.project_id)
    try:
        clip, created = SourceClipService().enqueue_clip(
            selection,
            form.cleaned_data["source_asset"],
            request.user,
            padding_before=form.cleaned_data["padding_before_seconds"],
            padding_after=form.cleaned_data["padding_after_seconds"],
        )
    except ProductionServiceError as exc:
        messages.error(request, str(exc))
        return redirect("production:project_clips", project_pk=selection.project_id)
    messages.success(
        request,
        "Clip queued. Processing will continue in the background." if created else "Existing clip or queued job reused.",
    )
    return redirect("production:clip_detail", clip_pk=clip.pk)


@login_required
def clip_detail(request, clip_pk):
    clip = get_object_or_404(clip_queryset_for(request.user), pk=clip_pk)
    is_stale = False
    if clip.status in {SourceClip.Status.VALIDATED, SourceClip.Status.APPROVED}:
        try:
            is_stale = SourceClipService.is_stale(clip)
        except ProductionServiceError:
            is_stale = True
    return render(
        request,
        "production/clip_detail.html",
        {"clip": clip, "is_stale": is_stale,
         "can_retry": clip.pipeline_job.status == PipelineJob.Status.FAILED
             and clip.pipeline_job.attempt_count < clip.pipeline_job.max_attempts
             and not SourceClipService.is_stale(clip)
             and clip.selected_segment.retired_at is None,
         "can_regenerate": clip.selected_segment.retired_at is None
             and clip.project.status == VideoProject.Status.APPROVED and not clip.project.is_locked},
    )


@login_required
@require_POST
def approve_source_clip(request, clip_pk):
    clip = get_object_or_404(clip_queryset_for(request.user), pk=clip_pk)
    try:
        SourceClipService.approve(clip, request.user)
    except ProductionServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Source clip approved.")
    return redirect("production:clip_detail", clip_pk=clip.pk)


def _bounded_file_iterator(file_object, remaining, block_size=64 * 1024):
    try:
        while remaining > 0:
            chunk = file_object.read(min(block_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file_object.close()


@login_required
def stream_source_clip(request, clip_pk):
    clip = get_object_or_404(clip_queryset_for(request.user), pk=clip_pk)
    if not clip.processed_asset_id or not clip.processed_asset.file:
        raise Http404("Clip media is unavailable.")
    asset = clip.processed_asset
    return stream_media_asset(request, asset)


@login_required
def stream_narration(request, take_pk):
    takes = NarrationTake.objects.select_related("project", "media_asset")
    if not (request.user.is_staff or request.user.is_superuser):
        takes = takes.filter(project__owner=request.user)
    take = get_object_or_404(takes, pk=take_pk)
    if not take.media_asset.file:
        raise Http404("Narration media is unavailable.")
    return stream_media_asset(request, take.media_asset)


def stream_media_asset(request, asset):
    """Serve private media after the calling view has checked project access."""
    try:
        file_object = asset.file.storage.open(asset.file.name, "rb")
    except (FileNotFoundError, OSError):
        raise Http404("Clip media is unavailable.")
    size = asset.byte_size
    content_type = asset.detected_mime_type or "video/mp4"
    range_header = request.headers.get("Range", "")
    match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header.strip())
    if match and size:
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else size - 1
        if start >= size or end < start:
            file_object.close()
            response = StreamingHttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        end = min(end, size - 1)
        try:
            file_object.seek(start)
        except (AttributeError, OSError):
            file_object.close()
            raise Http404("Clip media cannot be streamed.")
        length = end - start + 1
        response = StreamingHttpResponse(
            _bounded_file_iterator(file_object, length),
            status=206,
            content_type=content_type,
        )
        response["Content-Length"] = str(length)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    else:
        response = FileResponse(file_object, content_type=content_type)
        if size is not None:
            response["Content-Length"] = str(size)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def project_jobs(request, project_pk):
    project = get_object_or_404(project_queryset_for(request.user), pk=project_pk)
    jobs = project.pipeline_jobs.select_related("source_clip").order_by("-created_at")[:100]
    return render(request, "production/project_jobs.html", {"project": project, "jobs": jobs})


def job_queryset_for(user):
    queryset = PipelineJob.objects.select_related("project")
    return queryset if user.is_staff or user.is_superuser else queryset.filter(project__owner=user)


@login_required
@require_POST
def retry_job(request, job_pk):
    job = get_object_or_404(job_queryset_for(request.user), pk=job_pk)
    try:
        PipelineJobService.retry(job, request.user)
    except ProductionServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Job queued for another attempt.")
    return redirect("production:project_jobs", project_pk=job.project_id)


@login_required
@require_POST
def cancel_job(request, job_pk):
    job = get_object_or_404(job_queryset_for(request.user), pk=job_pk)
    try:
        PipelineJobService.cancel(job, request.user)
    except ProductionServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Job cancelled.")
    return redirect("production:project_jobs", project_pk=job.project_id)
