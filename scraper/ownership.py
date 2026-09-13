from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, render, redirect
from django.http import Http404
from .models import VideoProject, WorkflowAudit
from .services import lock_project


class TransferForm(forms.Form):
    owner = forms.ModelChoiceField(queryset=get_user_model().objects.none())
    reason = forms.CharField(max_length=2000, widget=forms.Textarea)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = get_user_model().objects.filter(is_active=True).exclude(username="__legacy_transcript_import__")


def transfer(project, owner, actor, reason):
    if not actor.is_active or not (actor.is_staff or actor.is_superuser):
        raise ValueError("Staff access is required.")
    with transaction.atomic():
        project = lock_project(project.pk)
        owner = get_user_model().objects.get(pk=owner.pk)
        if not project.source_videos.filter(legacy_scraped_video_id__isnull=False).exists():
            raise ValueError("Only imported projects can be reassigned here.")
        if not owner.is_active or owner.username == "__legacy_transcript_import__" or not reason.strip():
            raise ValueError("Select an active owner and provide a reason.")
        if project.owner_id == owner.pk:
            raise ValueError("This user already owns the project.")
        previous = project.owner_id
        project.owner = owner
        project.save(update_fields=["owner", "updated_at"])
        WorkflowAudit.objects.create(project=project, actor=actor, action="import_owner_transferred",
            detail={"previous_owner": previous, "new_owner": owner.pk, "reason": reason.strip()})


@login_required
def reassign(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        raise Http404
    project = get_object_or_404(VideoProject, pk=pk)
    form = TransferForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            transfer(project, form.cleaned_data["owner"], request.user, form.cleaned_data["reason"])
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("project_detail", pk=pk)
    return render(request, "editorial/correction_form.html", {"project": project, "form": form, "heading": "Reassign imported project"})
