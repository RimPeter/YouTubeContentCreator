from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from scraper.models import SourceVideo, VideoProject

from .forms import AnalysisConfigurationForm, SegmentSelectionForm
from .models import AnalysisRun, AnalysisSegment, SegmentSelection
from .services import (
    AnalysisService,
    AnalysisServiceError,
    SelectionService,
    SelectionServiceError,
)


def project_queryset_for(user):
    queryset = VideoProject.objects.select_related("owner")
    return queryset if user.is_staff or user.is_superuser else queryset.filter(owner=user)


def source_queryset_for(user):
    queryset = SourceVideo.objects.select_related("project", "project__owner")
    return queryset if user.is_staff or user.is_superuser else queryset.filter(project__owner=user)


def run_queryset_for(user):
    queryset = AnalysisRun.objects.select_related(
        "source_video",
        "source_video__project",
        "source_video__project__owner",
        "created_by",
    )
    return queryset if user.is_staff or user.is_superuser else queryset.filter(
        source_video__project__owner=user
    )


def segment_queryset_for(user):
    queryset = AnalysisSegment.objects.select_related(
        "analysis_run__source_video__project",
        "start_chunk",
        "end_chunk",
    )
    return queryset if user.is_staff or user.is_superuser else queryset.filter(
        analysis_run__source_video__project__owner=user
    )


def selection_queryset_for(user):
    queryset = SegmentSelection.objects.active().select_related(
        "project",
        "project__owner",
        "analysis_segment__analysis_run__source_video",
    )
    return queryset if user.is_staff or user.is_superuser else queryset.filter(project__owner=user)


@login_required
def project_dashboard(request, project_pk):
    project = get_object_or_404(project_queryset_for(request.user), pk=project_pk)
    sources = list(project.source_videos.prefetch_related("analysis_runs").order_by("created_at"))
    for source in sources:
        for run in source.analysis_runs.all():
            run.is_stale_for_display = AnalysisService.is_stale(run)
    selections = project.segment_selections.active().select_related(
        "analysis_segment__analysis_run__source_video"
    ).order_by("order")
    return render(
        request,
        "analysis/project_dashboard.html",
        {
            "project": project,
            "sources": sources,
            "selections": selections,
            "configuration_form": AnalysisConfigurationForm(),
        },
    )


@login_required
@require_POST
def run_analysis(request, source_pk):
    source_video = get_object_or_404(source_queryset_for(request.user), pk=source_pk)
    form = AnalysisConfigurationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Analysis configuration is invalid.")
        return redirect("analysis:project_dashboard", project_pk=source_video.project_id)
    try:
        run = AnalysisService().analyze(
            source_video,
            request.user,
            configuration=form.cleaned_data,
        )
    except AnalysisServiceError as exc:
        messages.error(request, str(exc))
        return redirect("analysis:project_dashboard", project_pk=source_video.project_id)
    if run.used_fallback:
        messages.warning(request, "Analysis completed with deterministic fallback segmentation.")
    else:
        messages.success(request, "Transcript analysis completed.")
    return redirect("analysis:run_detail", run_pk=run.pk)


@login_required
def run_detail(request, run_pk):
    run = get_object_or_404(run_queryset_for(request.user), pk=run_pk)
    sort = request.GET.get("sort", "source")
    segments = run.segments.select_related("start_chunk", "end_chunk")
    if sort == "score":
        segments = segments.order_by("-aggregate_score", "order")
    else:
        sort = "source"
        segments = segments.order_by("order")
    selected = {
        selection.analysis_segment_id: selection
        for selection in SegmentSelection.objects.active().filter(
            project=run.source_video.project,
            analysis_segment__analysis_run=run,
        )
    }
    segment_rows = []
    for segment in segments:
        selection = selected.get(segment.pk)
        initial = {}
        if selection:
            initial = {
                "reviewed_start_seconds": selection.reviewed_start_seconds,
                "reviewed_end_seconds": selection.reviewed_end_seconds,
                "notes": selection.notes,
            }
        segment_rows.append(
            {
                "segment": segment,
                "selection": selection,
                "form": SegmentSelectionForm(initial=initial),
            }
        )
    return render(
        request,
        "analysis/run_detail.html",
        {
            "run": run,
            "segment_rows": segment_rows,
            "sort": sort,
            "is_stale": AnalysisService.is_stale(run),
        },
    )


@login_required
@require_POST
def select_segment(request, segment_pk):
    segment = get_object_or_404(segment_queryset_for(request.user), pk=segment_pk)
    project = segment.analysis_run.source_video.project
    form = SegmentSelectionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Selection boundaries or notes are invalid.")
        return redirect("analysis:run_detail", run_pk=segment.analysis_run_id)
    try:
        _selection, created = SelectionService.select(
            project,
            segment,
            request.user,
            reviewed_start=form.cleaned_data["reviewed_start_seconds"],
            reviewed_end=form.cleaned_data["reviewed_end_seconds"],
            notes=form.cleaned_data["notes"],
        )
    except SelectionServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Segment selected." if created else "Selection updated.")
    return redirect("analysis:run_detail", run_pk=segment.analysis_run_id)


@login_required
@require_POST
def deselect_segment(request, selection_pk):
    selection = get_object_or_404(selection_queryset_for(request.user), pk=selection_pk)
    run_pk = selection.analysis_segment.analysis_run_id
    try:
        SelectionService.deselect(selection.project, selection, request.user)
    except SelectionServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Segment deselected.")
    return redirect("analysis:run_detail", run_pk=run_pk)


@login_required
@require_POST
def reorder_selections(request, project_pk):
    project = get_object_or_404(project_queryset_for(request.user), pk=project_pk)
    current = list(project.segment_selections.active().order_by("order"))
    try:
        positions = []
        for selection in current:
            position = int(request.POST.get(f"position_{selection.pk}", ""))
            positions.append((position, selection.pk))
        if {position for position, _selection_id in positions} != set(
            range(1, len(current) + 1)
        ):
            raise ValueError
        ordered_ids = [selection_id for _position, selection_id in sorted(positions)]
        SelectionService.reorder(project, ordered_ids, request.user)
    except (ValueError, SelectionServiceError):
        messages.error(request, "Selection order must use every position exactly once.")
    else:
        messages.success(request, "Selection order updated.")
    return redirect("analysis:project_dashboard", project_pk=project.pk)
