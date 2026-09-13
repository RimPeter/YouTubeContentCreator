from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from .models import ResearchPackage, EvidenceSource, ReactionSequencePlan, TimelineNarrationTake
from .forms import ResearchPackageForm, EvidenceSourceForm
from .services.access import EditorialServiceError
from .services import crud


def owned(request, model, path):
    qs = model.objects.all()
    if not (request.user.is_staff or request.user.is_superuser):
        qs = qs.filter(**{path: request.user})
    return qs


@login_required
def correction(request, kind, pk):
    evidence = kind == "evidence"
    model = EvidenceSource if evidence else ResearchPackage
    obj = get_object_or_404(owned(request, model, "research_package__project__owner" if evidence else "project__owner"), pk=pk)
    form = (EvidenceSourceForm if evidence else ResearchPackageForm)(request.POST or None, instance=obj)
    package = obj.research_package if evidence else obj
    if request.method == "POST" and form.is_valid():
        try:
            (crud.correct_evidence if evidence else crud.correct_research)(obj, request.user, form.cleaned_data)
        except (EditorialServiceError, ValidationError) as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("editorial:research_detail", package.pk)
    return render(request, "editorial/correction_form.html", {"form": form, "project": package.project,
        "heading": "Edit evidence (changes require verification)" if evidence else "Edit draft research"})


@login_required
@require_POST
def revise(request, kind, pk):
    if kind not in {"plan", "research"}:
        from django.http import Http404
        raise Http404
    model = ReactionSequencePlan if kind == "plan" else ResearchPackage
    obj = get_object_or_404(owned(request, model, "project__owner"), pk=pk)
    route = "editorial:reaction_plan_detail" if kind == "plan" else "editorial:research_detail"
    try:
        new = (crud.revise_plan if kind == "plan" else crud.revise_research)(obj, request.user)
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
        return redirect(route, pk)
    return redirect(route, new.pk)


@login_required
@require_POST
def withdraw(request, pk):
    take = get_object_or_404(owned(request, TimelineNarrationTake, "timeline_item__timeline__project__owner"), pk=pk)
    try:
        crud.withdraw_take(take, request.user, revoke=request.POST.get("action") == "revoke")
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
    return redirect("editorial:recording_review", take.timeline_item.timeline_id)


@login_required
@require_POST
def select_take(request, pk):
    from .services.reaction_production import ReactionProductionService
    take = get_object_or_404(owned(request, TimelineNarrationTake, "timeline_item__timeline__project__owner"), pk=pk)
    try:
        ReactionProductionService.approve_take(take, request.user, select_only=True)
    except EditorialServiceError as exc:
        messages.error(request, str(exc))
    return redirect("editorial:recording_review", take.timeline_item.timeline_id)
