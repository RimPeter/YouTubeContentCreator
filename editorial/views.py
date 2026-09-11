from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from production.models import SourceClip, PipelineJob
from scraper.models import VideoProject

from .forms import EvidenceSourceForm, ReactionBlockForm, ReactionClaimForm, ResearchPackageForm
from .models import EvidenceSource, ReactionBlock, ReactionClaim, ResearchPackage
from .services.access import EditorialServiceError
from .services.reactions import ReactionService
from .services.research import ResearchService
from .services.research_suggestions import suggestions_for_clip
from .services.ai_research import AIResearchService, render_report
from .services.ai_reactions import AIReactionService


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
    return render(request, "editorial/project_editorial.html", {"project": project, "clips": clips})


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
        "claim_form": ReactionClaimForm(block=block),
    })


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
