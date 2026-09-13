from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from production.models import SourceClip, PipelineJob
from production.forms import NarrationUploadForm
from production.services.narration import NarrationService
from production.services.access import ProductionServiceError
from scraper.models import VideoProject

from .forms import (
    EvidenceSourceForm, ReactionBlockForm, ReactionClaimForm, ReactionSequencePlanForm,
    ReactionSequenceSectionForm, CreatorTimelineItemForm, SourceTimelineItemForm, ResearchPackageForm,
)
from .models import (EvidenceSource, ReactionBlock, ReactionClaim, ReactionSequenceDraft,
                     ReactionProductionAssembly, ReactionSequencePlan, ReactionTimeline, ReactionTimelineItem,
                     TimelineNarrationTake, ResearchPackage)
from .services.access import EditorialServiceError
from .services.reactions import ReactionService
from .services.research import ResearchService
from .services.research_suggestions import suggestions_for_clip
from .services.ai_research import AIResearchService, render_report
from .services.ai_reactions import AIReactionService
from .services.reaction_sequence_plans import ReactionSequencePlanService
from .services.reaction_sequence_drafts import ReactionSequenceDraftService
from .services.reaction_timelines import ReactionTimelineService
from .services.reaction_production import ReactionProductionService


def _owned(queryset, user):
    return queryset if user.is_staff or user.is_superuser else queryset.filter(project__owner=user)


def project_qs(user):
    queryset = VideoProject.objects.select_related("owner")
    return queryset if user.is_staff or user.is_superuser else queryset.filter(owner=user)


def clip_qs(user):
    return _owned(SourceClip.objects.select_related("project", "processed_asset"), user)


def package_qs(user):
    return _owned(ResearchPackage.objects.select_related("project", "source_clip"), user)


def block_qs(user):
    return _owned(ReactionBlock.objects.select_related("project", "source_clip", "research_package"), user)


def plan_qs(user):
    return _owned(ReactionSequencePlan.objects.select_related("project", "created_by"), user)


def _result(request, action, success, route, identifier):
    try:
        action()
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, success)
    return redirect(route, identifier)


@login_required
def project_editorial(request, project_pk):
    project = get_object_or_404(project_qs(request.user), pk=project_pk)
    clips = project.source_clips.filter(status=SourceClip.Status.APPROVED).select_related(
        "selected_segment__analysis_segment"
    )
    plans = project.reaction_sequence_plans.prefetch_related("sections").all()
    return render(request, "editorial/project_editorial.html", {"project": project, "clips": clips, "plans": plans})


@login_required
@require_POST
def create_reaction_plan(request, project_pk):
    project = get_object_or_404(project_qs(request.user), pk=project_pk)
    try:
        plan = ReactionSequencePlanService.create(project, request.user)
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
        return redirect("editorial:project_editorial", project.pk)
    messages.success(request, "Reaction sequence plan created for review.")
    return redirect("editorial:reaction_plan_detail", plan.pk)


@login_required
def reaction_plan_detail(request, plan_pk):
    plan = get_object_or_404(plan_qs(request.user), pk=plan_pk)
    is_stale = ReactionSequencePlanService.is_stale(plan)
    sections = list(plan.sections.select_related("selection__analysis_segment").all())
    return render(request, "editorial/reaction_plan_detail.html", {
        "plan": plan,
        "section_rows": [
            {"section": section, "form": ReactionSequenceSectionForm(instance=section)}
            for section in sections
        ],
        "plan_form": ReactionSequencePlanForm(instance=plan),
        "is_stale": is_stale,
        "drafts": plan.reaction_drafts.all(),
    })


@login_required
@require_POST
def edit_reaction_plan(request, plan_pk):
    plan = get_object_or_404(plan_qs(request.user), pk=plan_pk)
    form = ReactionSequencePlanForm(request.POST, instance=plan)
    if not form.is_valid():
        messages.error(request, "The reaction plan details are invalid.")
        return redirect("editorial:reaction_plan_detail", plan.pk)
    return _result(request, lambda: ReactionSequencePlanService.update_plan(plan, request.user, form.cleaned_data),
                   "Reaction plan updated.", "editorial:reaction_plan_detail", plan.pk)


@login_required
@require_POST
def ready_reaction_plan(request, plan_pk):
    plan = get_object_or_404(plan_qs(request.user), pk=plan_pk)
    return _result(request, lambda: ReactionSequencePlanService.mark_ready(
        plan, request.user, research_reviewed=request.POST.get("research_reviewed") == "on"
    ), "Reaction plan is ready for drafting.", "editorial:reaction_plan_detail", plan.pk)


@login_required
@require_POST
def generate_reaction_sequence_draft(request, plan_pk):
    plan = get_object_or_404(plan_qs(request.user), pk=plan_pk)
    try:
        draft = ReactionSequenceDraftService.generate(plan, request.user, use_ai=request.POST.get("mode") != "basic")
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
        return redirect("editorial:reaction_plan_detail", plan.pk)
    messages.success(request, "Continuous reaction draft generated for human review.")
    return redirect("editorial:reaction_sequence_draft_detail", draft.pk)


@login_required
def reaction_sequence_draft_detail(request, draft_pk):
    drafts = _owned(ReactionSequenceDraft.objects.select_related("project", "plan"), request.user)
    draft = get_object_or_404(drafts, pk=draft_pk)
    return render(request, "editorial/reaction_sequence_draft_detail.html", {"draft": draft, "sections": draft.sections.select_related("plan_section").all()})


@login_required
@require_POST
def create_reaction_timeline(request, draft_pk):
    draft = get_object_or_404(
        _owned(ReactionSequenceDraft.objects.select_related("project", "plan"), request.user), pk=draft_pk,
    )
    try:
        timeline = ReactionTimelineService.create(draft, request.user)
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
        return redirect("editorial:reaction_sequence_draft_detail", draft.pk)
    messages.success(request, "Reaction Timeline created with source and creator transcript turns.")
    return redirect("editorial:reaction_timeline_detail", timeline.pk)


@login_required
def reaction_timeline_detail(request, timeline_pk):
    timeline = get_object_or_404(
        _owned(ReactionTimeline.objects.select_related("project", "reaction_draft__plan"), request.user),
        pk=timeline_pk,
    )
    view = request.GET.get("view", "timeline")
    if view not in {"timeline", "source", "creator"}:
        view = "timeline"
    items = timeline.items.select_related("plan_section", "draft_section").all()
    if view != "timeline":
        items = items.filter(item_type=view)
    is_stale = ReactionTimelineService.is_stale(timeline)
    ReactionProductionService.sync_timeline_status(timeline)
    return render(request, "editorial/reaction_timeline_detail.html", {
        "timeline": timeline, "items": items, "view": view,
        "is_stale": is_stale,
        "narration_form": NarrationUploadForm(),
        "readiness_issues": ReactionProductionService.readiness(timeline)[0],
    })


def _timeline_item_for(user, item_pk):
    items = ReactionTimelineItem.objects.select_related("timeline__project")
    if not (user.is_staff or user.is_superuser):
        items = items.filter(timeline__project__owner=user)
    return get_object_or_404(items, pk=item_pk)


@login_required
@require_POST
def edit_timeline_creator_item(request, item_pk):
    item = _timeline_item_for(request.user, item_pk)
    form = CreatorTimelineItemForm(request.POST)
    if form.is_valid():
        try:
            ReactionTimelineService.update_creator_item(item, request.user, **form.cleaned_data)
        except EditorialServiceError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Creator transcript turn updated.")
    else:
        messages.error(request, "Creator transcript turn needs a label and text.")
    return redirect("editorial:reaction_timeline_detail", item.timeline_id)


@login_required
@require_POST
def add_timeline_creator_item(request, timeline_pk):
    timeline = get_object_or_404(
        _owned(ReactionTimeline.objects.select_related("project"), request.user), pk=timeline_pk,
    )
    form = CreatorTimelineItemForm(request.POST)
    if form.is_valid():
        try:
            ReactionTimelineService.add_creator_item(timeline, request.user, label=form.cleaned_data['label'], transcript_text=form.cleaned_data['transcript_text'])
        except EditorialServiceError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Creator add-on added at the end of the timeline.")
    else:
        messages.error(request, "Creator transcript turn needs a label and text.")
    return redirect("editorial:reaction_timeline_detail", timeline.pk)


@login_required
@require_POST
def edit_timeline_source_item(request, item_pk):
    item = _timeline_item_for(request.user, item_pk)
    form = SourceTimelineItemForm(request.POST)
    if form.is_valid():
        try:
            ReactionTimelineService.update_source_item(item, request.user, **form.cleaned_data)
        except EditorialServiceError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Source trim updated; its saved transcript remains unchanged.")
    else:
        messages.error(request, "Enter a valid source in and out time.")
    return redirect("editorial:reaction_timeline_detail", item.timeline_id)


@login_required
@require_POST
def move_timeline_item(request, item_pk):
    item = _timeline_item_for(request.user, item_pk)
    try:
        ReactionTimelineService.move_item(item, request.user, request.POST.get("direction"))
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
    return redirect("editorial:reaction_timeline_detail", item.timeline_id)


@login_required
@require_POST
def upload_timeline_narration(request, item_pk):
    item = _timeline_item_for(request.user, item_pk)
    form = NarrationUploadForm(request.POST, request.FILES, prefix=str(item.pk) if request.POST.get("recording_screen") else None)
    if not form.is_valid():
        messages.error(request, "Choose a valid narration file and confirm its usage rights.")
    else:
        try:
            ReactionProductionService.upload_take(item, form.cleaned_data["media_file"], request.user,
                rights_basis=form.cleaned_data["rights_basis"], rights_notes=form.cleaned_data["rights_notes"],
                rights_confirmed=form.cleaned_data["rights_confirmed"], recording_notes=request.POST.get('recording_notes', '')[:5000])
        except (EditorialServiceError, ProductionServiceError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Narration uploaded. Approve it after listening.")
    return redirect("editorial:recording_review", item.timeline_id)


@login_required
@require_POST
def approve_timeline_narration(request, take_pk):
    takes = TimelineNarrationTake.objects.select_related("timeline_item__timeline__project")
    if not (request.user.is_staff or request.user.is_superuser):
        takes = takes.filter(timeline_item__timeline__project__owner=request.user)
    take = get_object_or_404(takes, pk=take_pk)
    try:
        ReactionProductionService.approve_take(take, request.user)
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Narration take approved for this creator turn.")
    return redirect("editorial:recording_review", take.timeline_item.timeline_id)


@login_required
@require_POST
def create_reaction_assembly(request, timeline_pk):
    timeline = get_object_or_404(_owned(ReactionTimeline.objects.select_related("project"), request.user), pk=timeline_pk)
    try:
        assembly = ReactionProductionService.create_assembly(timeline, request.user)
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
        return redirect("editorial:reaction_timeline_detail", timeline.pk)
    messages.success(request, "Production assembly created for handoff.")
    return redirect("editorial:reaction_assembly_detail", assembly.pk)


@login_required
def reaction_assembly_detail(request, assembly_pk):
    assemblies = ReactionProductionAssembly.objects.select_related("timeline__project")
    if not (request.user.is_staff or request.user.is_superuser):
        assemblies = assemblies.filter(timeline__project__owner=request.user)
    assembly = get_object_or_404(assemblies, pk=assembly_pk)
    return render(request, "editorial/reaction_assembly_detail.html", {
        "assembly": assembly, "items": assembly.items.select_related("narration_take", "source_clip").all(),
        "is_stale": ReactionProductionService.is_assembly_stale(assembly),
        "total_duration": sum(i.snapshot.get("duration", 0) for i in assembly.items.all()),
    })


@login_required
@require_POST
def review_recording_script(request, timeline_pk):
    timeline = get_object_or_404(_owned(ReactionTimeline.objects.all(), request.user), pk=timeline_pk)
    return _result(request, lambda: ReactionProductionService.review(timeline, request.user),
                   "Script reviewed and ready for recording.", "editorial:recording_review", timeline.pk)


@login_required
def recording_review(request, timeline_pk):
    timeline = get_object_or_404(_owned(ReactionTimeline.objects.select_related("project"), request.user), pk=timeline_pk)
    issues, resolved = ReactionProductionService.readiness(timeline)
    ReactionProductionService.sync_timeline_status(timeline)
    rows, source_duration, narration_duration, estimated = [], 0, 0, 0
    for item, take, clip in resolved:
        words = len(item.transcript_text.split())
        duration = words / 150 * 60
        takes = list(item.narration_takes.select_related("media_asset", "created_by"))
        for historical in takes:
            historical.is_current = historical.script_fingerprint == ReactionProductionService.script_fingerprint(item)
        if item.item_type == "source":
            duration = max(0, (item.source_end_seconds or 0) - (item.source_start_seconds or 0))
            source_duration += duration
        elif take:
            duration = float(take.media_asset.duration_seconds)
            narration_duration += duration
        estimated += duration
        rows.append({"item": item, "take": take, "clip": clip, "takes": takes, "words": words,
                     "duration": duration, "form": NarrationUploadForm(prefix=str(item.pk))})
    return render(request, "editorial/recording_review.html", {
        "timeline": timeline, "rows": rows, "issues": issues, "source_duration": source_duration,
        "narration_duration": narration_duration, "estimated": estimated,
        "assemblies": timeline.production_assemblies.all(),
    })


@login_required
def timeline_take_media(request, take_pk):
    from production.views import stream_media_asset
    takes = TimelineNarrationTake.objects.select_related("media_asset", "timeline_item__timeline")
    if not (request.user.is_staff or request.user.is_superuser):
        takes = takes.filter(timeline_item__timeline__project__owner=request.user)
    take = get_object_or_404(takes, pk=take_pk)
    return stream_media_asset(request, take.media_asset)


@login_required
def recording_script_download(request, timeline_pk):
    from django.http import HttpResponse
    timeline = get_object_or_404(_owned(ReactionTimeline.objects.all(), request.user), pk=timeline_pk)
    lines = [f"Reaction Timeline v{timeline.version}", ""]
    for item in timeline.items.filter(included=True):
        lines.append(f"#{item.order} {item.get_item_type_display()}: {item.label}")
        if item.item_type == "source":
            lines.append(f"Play source {item.source_start_seconds}s to {item.source_end_seconds}s (full context below)")
        lines.extend([item.transcript_text, ""])
    response = HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="recording-script-{timeline.pk}.txt"'
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def assembly_script_download(request, assembly_pk):
    from django.http import HttpResponse
    assemblies = ReactionProductionAssembly.objects.all()
    if not (request.user.is_staff or request.user.is_superuser):
        assemblies = assemblies.filter(timeline__project__owner=request.user)
    assembly = get_object_or_404(assemblies, pk=assembly_pk)
    lines = [f"Assembly v{assembly.version} — saved recording script", ""]
    for item in assembly.items.all():
        lines.extend([f"#{item.order} {item.snapshot.get('type', '')}: {item.snapshot.get('label', '')}",
                      item.transcript_text, ""])
    response = HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="assembly-script-{assembly.pk}.txt"'
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def edit_reaction_plan_section(request, section_pk):
    from .models import ReactionSequenceSection
    sections = ReactionSequenceSection.objects.select_related("plan__project")
    if not (request.user.is_staff or request.user.is_superuser):
        sections = sections.filter(plan__project__owner=request.user)
    section = get_object_or_404(sections, pk=section_pk)
    form = ReactionSequenceSectionForm(request.POST, instance=section)
    if not form.is_valid():
        messages.error(request, "The reaction plan section is invalid.")
        return redirect("editorial:reaction_plan_detail", section.plan_id)
    return _result(request, lambda: ReactionSequencePlanService.update_section(section, request.user, form.cleaned_data),
                   "Reaction plan section updated.", "editorial:reaction_plan_detail", section.plan_id)


@login_required
def research_suggestions(request, clip_pk):
    clip = get_object_or_404(
        clip_qs(request.user).select_related("selected_segment__analysis_segment"), pk=clip_pk,
    )
    try:
        suggestions = suggestions_for_clip(clip, request.user)
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
        return redirect("editorial:project_editorial", clip.project_id)
    return render(request, "editorial/research_suggestions.html", {
        "clip": clip, "suggestions": suggestions,
        "transcript": clip.selected_segment.analysis_segment.source_text,
    })


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def create_research(request, clip_pk):
    clip = get_object_or_404(clip_qs(request.user), pk=clip_pk)
    if request.method in {"GET", "HEAD"}:
        url = reverse("editorial:project_editorial", args=[clip.project_id])
        return redirect(f"{url}#research-clip-{clip.pk}")
    form = ResearchPackageForm(request.POST)
    if form.is_valid():
        try:
            package = ResearchService.create_package(clip, request.user, **form.cleaned_data)
        except EditorialServiceError as exc:
            messages.error(request, str(exc))
        else:
            return redirect("editorial:research_detail", package.pk)
    else:
        messages.error(request, "Provide a research question and editorial focus.")
    return redirect("editorial:project_editorial", clip.project_id)


@login_required
def research_detail(request, package_pk):
    package = get_object_or_404(package_qs(request.user), pk=package_pk)
    job = PipelineJob.objects.filter(
        project_id=package.project_id, job_type="ai_research", input_snapshot__package_id=package.pk,
    ).order_by("-created_at").first()
    report = package.configuration_snapshot.get("ai_research_report", {})
    reaction_job = PipelineJob.objects.filter(
        project_id=package.project_id, job_type="ai_reaction", input_snapshot__package_id=package.pk,
    ).order_by("-created_at").first()
    generated_reaction = package.reaction_blocks.filter(
        configuration_snapshot__pipeline_job_id=reaction_job.pk,
        status__in=["draft", "approved", "superseded"],
    ).order_by("-version").first() if reaction_job else None
    template = "editorial/_research_activity.html" if request.GET.get("activity") == "1" else "editorial/research_detail.html"
    if request.GET.get("activity") == "reaction":
        template = "editorial/_reaction_activity.html"
    response = render(request, template, {
        "package": package, "evidence_form": EvidenceSourceForm(),
        "reactions": package.reaction_blocks.all(),
        "research_job": job, "ai_configured": bool(settings.OPENAI_API_KEY),
        "reaction_job": reaction_job, "generated_reaction": generated_reaction,
        "ai_report": render_report(report) if report else "",
    })
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def run_ai_research(request, package_pk):
    package = get_object_or_404(package_qs(request.user), pk=package_pk)
    return _result(request, lambda: AIResearchService.enqueue(package, request.user),
                   "AI research requested. Follow its progress below; completed results are reused.",
                   "editorial:research_detail", package.pk)


@login_required
@require_POST
def add_evidence(request, package_pk):
    package = get_object_or_404(package_qs(request.user), pk=package_pk)
    form = EvidenceSourceForm(request.POST)
    if form.is_valid():
        try:
            ResearchService.add_evidence(package, request.user, **form.cleaned_data)
        except EditorialServiceError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Evidence added as unverified.")
    else:
        messages.error(request, "The evidence source is incomplete or invalid.")
    return redirect("editorial:research_detail", package.pk)


def _evidence_for(user, pk):
    queryset = EvidenceSource.objects.select_related("research_package__project")
    if not (user.is_staff or user.is_superuser):
        queryset = queryset.filter(research_package__project__owner=user)
    return get_object_or_404(queryset, pk=pk)


@login_required
@require_POST
def verify_evidence(request, evidence_pk):
    evidence = _evidence_for(request.user, evidence_pk)
    return _result(request, lambda: ResearchService.set_verification(evidence, request.user, verified=True),
                   "Evidence verified by you.", "editorial:research_detail", evidence.research_package_id)


@login_required
@require_POST
def reject_evidence(request, evidence_pk):
    evidence = _evidence_for(request.user, evidence_pk)
    return _result(request, lambda: ResearchService.set_verification(evidence, request.user, verified=False),
                   "Evidence rejected.", "editorial:research_detail", evidence.research_package_id)


@login_required
@require_POST
def delete_evidence(request, evidence_pk):
    evidence = _evidence_for(request.user, evidence_pk)
    package_id = evidence.research_package_id
    return _result(request, lambda: ResearchService.delete_evidence(evidence, request.user),
                   "Evidence removed.", "editorial:research_detail", package_id)


@login_required
@require_POST
def mark_research_ready(request, package_pk):
    package = get_object_or_404(package_qs(request.user), pk=package_pk)
    return _result(request, lambda: ResearchService.mark_ready(package, request.user),
                   "Research marked ready.", "editorial:research_detail", package.pk)


@login_required
@require_POST
def generate_reaction(request, package_pk):
    package = get_object_or_404(package_qs(request.user), pk=package_pk)
    if request.POST.get("mode") != "basic":
        return _result(request, lambda: AIReactionService.enqueue(package, request.user),
                       "AI reaction requested. Follow its progress below.", "editorial:research_detail", package.pk)
    try:
        block = ReactionService().generate(package, request.user)
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
        return redirect("editorial:research_detail", package.pk)
    messages.success(request, "Reaction draft generated for human review.")
    return redirect("editorial:reaction_detail", block.pk)


@login_required
def reaction_detail(request, block_pk):
    block = get_object_or_404(block_qs(request.user), pk=block_pk)
    return render(request, "editorial/reaction_detail.html", {
        "reaction": block, "edit_form": ReactionBlockForm(block=block),
        "claim_form": ReactionClaimForm(block=block), "narration_form": NarrationUploadForm(),
        "narration_takes": block.narration_takes.select_related("media_asset").all(),
    })


@login_required
@require_POST
def upload_narration(request, block_pk):
    block = get_object_or_404(block_qs(request.user), pk=block_pk)
    form = NarrationUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Choose a valid audio recording and confirm its usage rights.")
        return redirect("editorial:reaction_detail", block.pk)
    try:
        take = NarrationService.upload(
            block, form.cleaned_data["media_file"], request.user,
            rights_basis=form.cleaned_data["rights_basis"], rights_notes=form.cleaned_data["rights_notes"],
            rights_confirmed=form.cleaned_data["rights_confirmed"],
        )
    except (EditorialServiceError, ProductionServiceError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Narration take v{take.version} validated and ready for playback.")
    return redirect("editorial:reaction_detail", block.pk)


@login_required
@require_POST
def edit_reaction(request, block_pk):
    block = get_object_or_404(block_qs(request.user), pk=block_pk)
    form = ReactionBlockForm(request.POST, block=block)
    if form.is_valid():
        return _result(request, lambda: ReactionService.update_draft(block, request.user, **form.cleaned_data),
                       "Reaction draft updated.", "editorial:reaction_detail", block.pk)
    messages.error(request, "The reaction draft is incomplete or invalid.")
    return redirect("editorial:reaction_detail", block.pk)


@login_required
@require_POST
def add_claim(request, block_pk):
    block = get_object_or_404(block_qs(request.user), pk=block_pk)
    form = ReactionClaimForm(request.POST, block=block)
    if form.is_valid():
        return _result(request, lambda: ReactionService.add_claim(block, request.user, **form.cleaned_data),
                       "Claim added.", "editorial:reaction_detail", block.pk)
    messages.error(request, "The claim or citation is invalid.")
    return redirect("editorial:reaction_detail", block.pk)


@login_required
@require_POST
def delete_claim(request, claim_pk):
    queryset = ReactionClaim.objects.select_related("reaction_block__project")
    if not (request.user.is_staff or request.user.is_superuser):
        queryset = queryset.filter(reaction_block__project__owner=request.user)
    claim = get_object_or_404(queryset, pk=claim_pk)
    block_id = claim.reaction_block_id
    return _result(request, lambda: ReactionService.delete_claim(claim, request.user),
                   "Claim removed.", "editorial:reaction_detail", block_id)


@login_required
@require_POST
def approve_reaction(request, block_pk):
    block = get_object_or_404(block_qs(request.user), pk=block_pk)
    return _result(request, lambda: ReactionService.approve(
        block, request.user,
        evidence_reviewed=request.POST.get("evidence_reviewed") == "on",
        originality_confirmed=request.POST.get("originality_confirmed") == "on",
    ), "Reaction approved.", "editorial:reaction_detail", block.pk)
