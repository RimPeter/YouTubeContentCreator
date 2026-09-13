from django.core.exceptions import ValidationError
from django.db.models import Max
from django.utils import timezone

from analysis.models import SegmentSelection
from analysis.services import AnalysisService
from production.services.fingerprints import fingerprint_json

from editorial.models import ReactionSequencePlan, ReactionSequenceSection

from .access import EditorialServiceError, editorial_transaction, ensure_editorial_allowed


class ReactionSequencePlanService:
    ROLE_BY_APPROACH = {
        "review": ReactionSequenceSection.Role.EVIDENCE,
        "critic": ReactionSequenceSection.Role.TENSION,
        "reflect": ReactionSequenceSection.Role.REFLECTION,
        "add_on": ReactionSequenceSection.Role.ADD_ON,
        "explain": ReactionSequenceSection.Role.EVIDENCE,
        "verify": ReactionSequenceSection.Role.TENSION,
        "compare": ReactionSequenceSection.Role.TENSION,
        "question": ReactionSequenceSection.Role.REFLECTION,
        "pass": ReactionSequenceSection.Role.ADD_ON,
    }
    RESEARCH_APPROACHES = {"verify", "critic", "compare"}

    @classmethod
    def _selections(cls, project):
        selections = list(
            SegmentSelection.objects.active().filter(project=project).select_related(
                "analysis_segment__analysis_run__source_video"
            ).order_by("order")
        )
        if not selections:
            raise EditorialServiceError("Select at least one current segment before creating a reaction plan.")
        for selection in selections:
            run = selection.analysis_segment.analysis_run
            if AnalysisService.is_stale(run):
                raise EditorialServiceError("Every selected segment must come from a current analysis run.")
        return selections

    @staticmethod
    def _snapshot(selections):
        return [
            {
                "selection_id": selection.pk,
                "order": selection.order,
                "segment_id": selection.analysis_segment_id,
                "source_fingerprint": selection.analysis_segment.analysis_run.source_fingerprint,
                "start": selection.reviewed_start_seconds,
                "end": selection.reviewed_end_seconds,
                "notes": selection.notes,
                "recommendation": selection.analysis_segment.editorial_recommendation,
            }
            for selection in selections
        ]

    @classmethod
    def current_fingerprint(cls, project):
        return fingerprint_json({"reaction_sequence_plan": cls._snapshot(cls._selections(project))})

    @classmethod
    def is_stale(cls, plan):
        try:
            return plan.input_fingerprint != cls.current_fingerprint(plan.project)
        except EditorialServiceError:
            return True

    @classmethod
    def create(cls, project, user):
        with editorial_transaction(project.pk, user):
            ensure_editorial_allowed(project, user)
            selections = cls._selections(project)
            version = (ReactionSequencePlan.objects.filter(project=project).aggregate(latest=Max("version"))["latest"] or 0) + 1
            first = selections[0].analysis_segment
            last = selections[-1].analysis_segment
            plan = ReactionSequencePlan.objects.create(
                project=project,
                version=version,
                input_fingerprint=fingerprint_json({"reaction_sequence_plan": cls._snapshot(selections)}),
                overall_thesis=f"A continuous reaction to {first.title} and the ideas that follow.",
                audience_angle="Connect the selected ideas into one useful perspective for the audience.",
                planned_conclusion=f"Return to the central takeaway from {last.title}.",
                created_by=user,
            )
            sections = []
            for index, selection in enumerate(selections, start=1):
                segment = selection.analysis_segment
                recommendation = segment.editorial_recommendation or {}
                primary = recommendation.get("primary_approach", "add_on")
                role = cls.ROLE_BY_APPROACH.get(primary, ReactionSequenceSection.Role.ADD_ON)
                if index == 1:
                    role = ReactionSequenceSection.Role.THESIS
                elif index == len(selections):
                    role = ReactionSequenceSection.Role.CONCLUSION
                research_required = bool(recommendation.get("research_needed")) or primary in cls.RESEARCH_APPROACHES
                sections.append(ReactionSequenceSection(
                    plan=plan, selection=selection, order=index, role=role, source_title=segment.title,
                    transcript_snapshot=segment.source_text, recommendation_snapshot=recommendation,
                    bridge="" if index == 1 else f"Connect the previous idea to {segment.title}.",
                    research_required=research_required,
                    research_reason=("Verify or contextualize claims before drafting this section." if research_required else ""),
                ))
            ReactionSequenceSection.objects.bulk_create(sections)
            return plan

    @classmethod
    def _editable(cls, plan, user):
        ensure_editorial_allowed(plan.project, user)
        if plan.status != ReactionSequencePlan.Status.DRAFT:
            raise EditorialServiceError("Only current draft reaction plans can be edited.")
        if cls.is_stale(plan):
            raise EditorialServiceError(
                "Selected segments changed; create a new reaction plan.", "reaction_plan_stale"
            )

    @classmethod
    def update_plan(cls, plan, user, values):
        try:
            with editorial_transaction(plan.project_id, user):
                plan = ReactionSequencePlan.objects.select_for_update().select_related("project").get(pk=plan.pk)
                cls._editable(plan, user)
                for name in ("overall_thesis", "audience_angle", "planned_conclusion"):
                    setattr(plan, name, values[name].strip())
                try:
                    plan.full_clean()
                except ValidationError as exc:
                    raise EditorialServiceError("The reaction plan details are invalid.") from exc
                plan.save(update_fields=["overall_thesis", "audience_angle", "planned_conclusion", "updated_at"])
                return plan
        except EditorialServiceError as exc:
            if exc.code == "reaction_plan_stale":
                ReactionSequencePlan.objects.filter(pk=plan.pk).update(status=ReactionSequencePlan.Status.STALE)
            raise

    @classmethod
    def update_section(cls, section, user, values):
        try:
            with editorial_transaction(section.plan.project_id, user):
                section = ReactionSequenceSection.objects.select_for_update().select_related("plan__project").get(pk=section.pk)
                cls._editable(section.plan, user)
                section.role = values["role"]
                section.bridge = values["bridge"].strip()
                section.research_required = values["research_required"]
                section.research_reason = values["research_reason"].strip()
                try:
                    section.full_clean()
                except ValidationError as exc:
                    raise EditorialServiceError("The reaction plan section is invalid.") from exc
                section.save(update_fields=["role", "bridge", "research_required", "research_reason"])
                return section
        except EditorialServiceError as exc:
            if exc.code == "reaction_plan_stale":
                ReactionSequencePlan.objects.filter(pk=section.plan_id).update(status=ReactionSequencePlan.Status.STALE)
            raise

    @classmethod
    def mark_ready(cls, plan, user, *, research_reviewed):
        with editorial_transaction(plan.project_id, user):
            plan = ReactionSequencePlan.objects.select_for_update().select_related("project").get(pk=plan.pk)
            cls._editable(plan, user)
            has_research = plan.sections.filter(research_required=True).exists()
            if has_research and not research_reviewed:
                raise EditorialServiceError("Confirm that the flagged research needs have been reviewed before drafting.")
            plan.status = ReactionSequencePlan.Status.READY
            plan.reviewed_by = user
            plan.reviewed_at = timezone.now()
            plan.review_attestation = {"research_reviewed": bool(research_reviewed)}
            plan.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_attestation", "updated_at"])
            return plan
