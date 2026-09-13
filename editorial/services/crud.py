"""Corrections and editable revisions preserve reviewed artifacts."""
from django.core.exceptions import ValidationError
from django.utils import timezone
from editorial.models import ResearchPackage, EvidenceSource, ReactionSequencePlan, ReactionSequenceSection, TimelineNarrationTake
from .access import EditorialServiceError, editorial_transaction
from .research import ResearchService, validate_public_url
from .reaction_sequence_plans import ReactionSequencePlanService
from .reaction_production import ReactionProductionService


def audit(project_id, user, action, detail):
    from scraper.models import WorkflowAudit
    WorkflowAudit.objects.create(project_id=project_id, actor=user, action=action, detail=detail)


def correct_research(package, user, values):
    with editorial_transaction(package.project_id, user):
        package = ResearchPackage.objects.select_for_update().get(pk=package.pk)
        ResearchService._editable(package, user)
        for field in ("research_question", "editorial_focus"):
            setattr(package, field, values[field])
        package.full_clean()
        package.save(update_fields=["research_question", "editorial_focus", "updated_at"])
        audit(package.project_id, user, "research_edited", {"package": package.pk})
        return package


def correct_evidence(evidence, user, values):
    with editorial_transaction(evidence.research_package.project_id, user):
        evidence = EvidenceSource.objects.select_for_update().select_related("research_package").get(pk=evidence.pk)
        ResearchService._editable(evidence.research_package, user)
        allowed = {"source_url", "title", "publisher", "author", "publication_date", "retrieved_on",
                   "classification", "finding", "relevance", "permitted_excerpt", "snapshot_reference"}
        if set(values) - allowed:
            raise EditorialServiceError("Unexpected evidence fields.")
        values["source_url"] = validate_public_url(values["source_url"])
        changed = any(getattr(evidence, key) != value for key, value in values.items())
        for key, value in values.items():
            setattr(evidence, key, value)
        if changed:
            evidence.verification_status = "unverified"
            evidence.verified_by = None
            evidence.verified_at = None
        evidence.full_clean()
        evidence.save()
        audit(evidence.research_package.project_id, user, "evidence_edited", {"evidence": evidence.pk, "verification_reset": changed})
        return evidence


def revise_plan(plan, user):
    with editorial_transaction(plan.project_id, user):
        plan = ReactionSequencePlan.objects.get(pk=plan.pk)
        if ReactionSequencePlanService.is_stale(plan):
            raise EditorialServiceError("Selections changed; create a fresh plan from current selections.")
        new = ReactionSequencePlanService.create(plan.project, user)
        for field in ("overall_thesis", "audience_angle", "planned_conclusion"):
            setattr(new, field, getattr(plan, field))
        new.save()
        for section in plan.sections.all():
            new.sections.filter(selection_id=section.selection_id).update(
                role=section.role, bridge=section.bridge, research_required=section.research_required,
                research_reason=section.research_reason)
        audit(plan.project_id, user, "plan_revised", {"from": plan.pk, "to": new.pk})
        return new


def revise_research(package, user):
    with editorial_transaction(package.project_id, user):
        package = ResearchPackage.objects.select_related("source_clip").get(pk=package.pk)
        new = ResearchService.create_package(package.source_clip, user,
            research_question=package.research_question, editorial_focus=package.editorial_focus)
        new.configuration_snapshot["revised_from"] = package.pk
        new.save(update_fields=["configuration_snapshot"])
        fields = ("source_url", "title", "publisher", "author", "publication_date", "retrieved_on",
                  "classification", "finding", "relevance", "permitted_excerpt", "snapshot_reference")
        for evidence in package.evidence_sources.all():
            ResearchService.add_evidence(new, user, **{name: getattr(evidence, name) for name in fields})
        audit(package.project_id, user, "research_revised", {"from": package.pk, "to": new.pk})
        return new


def withdraw_take(take, user, *, revoke=False):
    from .reaction_timelines import ReactionTimelineService
    with editorial_transaction(take.timeline_item.timeline.project_id, user):
        take = TimelineNarrationTake.objects.select_for_update().select_related("timeline_item__timeline").get(pk=take.pk)
        timeline = take.timeline_item.timeline
        take.selected = False
        if revoke:
            take.approved_at = None
            take.approved_by = None
        take.save(update_fields=["selected", "approved_at", "approved_by"])
        audit(timeline.project_id, user, "narration_revoked" if revoke else "narration_deselected", {"take": take.pk})
        ReactionProductionService.sync_timeline_status(timeline, persist=True)
        for assembly in timeline.production_assemblies.all():
            if ReactionProductionService.is_assembly_stale(assembly):
                type(assembly).objects.filter(pk=assembly.pk).update(status="stale")
