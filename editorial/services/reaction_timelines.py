from django.db.models import Max

from editorial.models import ReactionTimeline, ReactionTimelineItem
from production.services.fingerprints import fingerprint_json

from .access import EditorialServiceError, editorial_transaction, ensure_editorial_allowed
from .reaction_sequence_plans import ReactionSequencePlanService
from editorial.limits import CREATOR_TEXT_LIMIT


class ReactionTimelineService:
    @staticmethod
    def check_revision(item, expected):
        if expected is not None and str(item.revision) != str(expected):
            raise EditorialServiceError("This turn changed in another tab. Compare your submission with the current version.", "edit_conflict")

    @staticmethod
    def changed(timeline):
        from .reaction_production import ReactionProductionService
        ReactionProductionService.sync_timeline_status(timeline, persist=True)
        for assembly in timeline.production_assemblies.all():
            if ReactionProductionService.is_assembly_stale(assembly):
                type(assembly).objects.filter(pk=assembly.pk).update(status="stale")

    @staticmethod
    def current_fingerprint(draft):
        plan = draft.plan
        return fingerprint_json({"draft": draft.input_fingerprint, "opening": draft.opening, "conclusion": draft.conclusion, "status": draft.status, "plan": plan.input_fingerprint,
                                 "sections": list(draft.sections.values_list("pk", "reaction_text", "bridge"))})

    @classmethod
    def is_stale(cls, timeline):
        return (timeline.input_fingerprint != cls.current_fingerprint(timeline.reaction_draft)
                or ReactionSequencePlanService.is_stale(timeline.reaction_draft.plan))

    @classmethod
    def create(cls, draft, user):
        with editorial_transaction(draft.project_id, user):
            draft = draft.__class__.objects.select_for_update().select_related("project", "plan").prefetch_related(
                "sections__plan_section__selection__analysis_segment"
            ).get(pk=draft.pk)
            ensure_editorial_allowed(draft.project, user)
            if draft.status != draft.Status.DRAFT or ReactionSequencePlanService.is_stale(draft.plan):
                raise EditorialServiceError("Reaction draft inputs changed; create a new continuous draft.")
            version = (ReactionTimeline.objects.filter(reaction_draft=draft).aggregate(latest=Max("version"))["latest"] or 0) + 1
            timeline = ReactionTimeline.objects.create(project=draft.project, reaction_draft=draft, version=version,
                input_fingerprint=cls.current_fingerprint(draft), created_by=user)
            items = [ReactionTimelineItem(timeline=timeline, order=1, item_type="creator", label="Creator opening", transcript_text=draft.opening)]
            order = 2
            for section in draft.sections.order_by("order"):
                plan_section = section.plan_section
                selection = plan_section.selection
                segment = selection.analysis_segment
                items.append(ReactionTimelineItem(timeline=timeline, order=order, item_type="source", plan_section=plan_section,
                    label=plan_section.source_title, transcript_text=plan_section.transcript_snapshot,
                    source_start_seconds=selection.reviewed_start_seconds if selection.reviewed_start_seconds is not None else segment.start_seconds,
                    source_end_seconds=selection.reviewed_end_seconds if selection.reviewed_end_seconds is not None else segment.end_seconds))
                order += 1
                creator_text = "\n\n".join(part for part in (section.reaction_text, section.bridge) if part)
                items.append(ReactionTimelineItem(timeline=timeline, order=order, item_type="creator", plan_section=plan_section,
                    draft_section=section, label=f"Creator reaction: {plan_section.source_title}", transcript_text=creator_text))
                order += 1
            items.append(ReactionTimelineItem(timeline=timeline, order=order, item_type="creator", label="Creator conclusion", transcript_text=draft.conclusion))
            ReactionTimelineItem.objects.bulk_create(items)
            return timeline

    @classmethod
    def _current_timeline(cls, timeline, user):
        timeline = ReactionTimeline.objects.select_for_update().select_related(
            "project", "reaction_draft__plan"
        ).get(pk=timeline.pk)
        ensure_editorial_allowed(timeline.project, user)
        if cls.is_stale(timeline):
            ReactionTimeline.objects.filter(pk=timeline.pk).update(status=ReactionTimeline.Status.STALE)
            raise EditorialServiceError("Reaction Timeline inputs changed; create a new timeline.")
        return timeline

    @classmethod
    def update_creator_item(cls, item, user, *, label, transcript_text, included, expected_revision=None):
        with editorial_transaction(item.timeline.project_id, user):
            timeline = cls._current_timeline(item.timeline, user)
            item = ReactionTimelineItem.objects.select_for_update().get(pk=item.pk, timeline=timeline)
            cls.check_revision(item, expected_revision)
            if item.item_type != ReactionTimelineItem.ItemType.CREATOR:
                raise EditorialServiceError("Only creator transcript turns can be edited.")
            item.label = label.strip()
            item.transcript_text = transcript_text.strip()
            item.included = included
            if not item.label or not item.transcript_text or len(item.transcript_text) > CREATOR_TEXT_LIMIT or len(item.label) > 255:
                raise EditorialServiceError("Creator transcript turns need a label and text.")
            item.revision += 1
            item.save(update_fields=["label", "transcript_text", "included", "revision"])
            cls.changed(timeline)
            return item

    @classmethod
    def update_source_item(cls, item, user, *, source_start_seconds, source_end_seconds, included, expected_revision=None):
        with editorial_transaction(item.timeline.project_id, user):
            timeline = cls._current_timeline(item.timeline, user)
            item = ReactionTimelineItem.objects.select_for_update().select_related(
                "plan_section__selection__analysis_segment"
            ).get(pk=item.pk, timeline=timeline)
            cls.check_revision(item, expected_revision)
            if item.item_type != ReactionTimelineItem.ItemType.SOURCE:
                raise EditorialServiceError("Only source transcript turns can be trimmed.")
            selection = item.plan_section.selection
            segment = selection.analysis_segment
            lower = selection.reviewed_start_seconds if selection.reviewed_start_seconds is not None else segment.start_seconds
            upper = selection.reviewed_end_seconds if selection.reviewed_end_seconds is not None else segment.end_seconds
            if not (lower <= source_start_seconds < source_end_seconds <= upper):
                raise EditorialServiceError("Source trims must remain inside the selected segment.")
            item.source_start_seconds = source_start_seconds
            item.source_end_seconds = source_end_seconds
            item.included = included
            item.revision += 1
            item.save(update_fields=["source_start_seconds", "source_end_seconds", "included", "revision"])
            cls.changed(timeline)
            return item

    @classmethod
    def move_item(cls, item, user, direction, expected_revision=None):
        if direction not in {"up", "down"}:
            raise EditorialServiceError("Choose a valid timeline direction.")
        with editorial_transaction(item.timeline.project_id, user):
            timeline = cls._current_timeline(item.timeline, user)
            item = ReactionTimelineItem.objects.select_for_update().get(pk=item.pk, timeline=timeline)
            cls.check_revision(item, expected_revision)
            target_order = item.order - 1 if direction == "up" else item.order + 1
            other = ReactionTimelineItem.objects.select_for_update().filter(
                timeline=timeline, order=target_order
            ).first()
            if other is None:
                return item
            temporary_order = timeline.items.aggregate(latest=Max("order"))["latest"] + 1
            ReactionTimelineItem.objects.filter(pk=item.pk).update(order=temporary_order)
            ReactionTimelineItem.objects.filter(pk=other.pk).update(order=item.order)
            ReactionTimelineItem.objects.filter(pk=item.pk).update(order=other.order)
            from django.db.models import F
            ReactionTimelineItem.objects.filter(pk__in=[item.pk, other.pk]).update(revision=F("revision") + 1)
            cls.changed(timeline)
            item.refresh_from_db()
            return item

    @classmethod
    def add_creator_item(cls, timeline, user, *, label, transcript_text):
        with editorial_transaction(timeline.project_id, user):
            timeline = cls._current_timeline(timeline, user)
            label = label.strip()
            transcript_text = transcript_text.strip()
            if not label or not transcript_text or len(label) > 255 or len(transcript_text) > CREATOR_TEXT_LIMIT:
                raise EditorialServiceError("Creator transcript turns need a label and text.")
            order = (timeline.items.aggregate(latest=Max("order"))["latest"] or 0) + 1
            item = ReactionTimelineItem.objects.create(
                timeline=timeline, order=order, item_type=ReactionTimelineItem.ItemType.CREATOR,
                label=label, transcript_text=transcript_text,
            )
            cls.changed(timeline)
            return item
