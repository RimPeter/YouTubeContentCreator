from django.db import transaction
from django.db.models import Max

from analysis.models import AnalysisRun, AnalysisSegment, SegmentSelection
from scraper.models import VideoProject

from .analysis import AnalysisService


class SelectionServiceError(Exception):
    pass


class SelectionPermissionError(SelectionServiceError):
    pass


class SelectionLifecycleError(SelectionServiceError):
    pass


class SelectionValidationError(SelectionServiceError):
    pass


class SelectionService:
    @staticmethod
    def ensure_user_access(project, user):
        if not user.is_authenticated or (
            project.owner_id != user.pk and not user.is_staff and not user.is_superuser
        ):
            raise SelectionPermissionError("You cannot modify selections for this project.")

    @classmethod
    def ensure_selection_allowed(cls, project, user):
        cls.ensure_user_access(project, user)
        if project.status != VideoProject.Status.APPROVED or project.is_locked:
            raise SelectionLifecycleError(
                "Selection requires an approved, unlocked, non-archived project."
            )

    @staticmethod
    def _validate_reviewed_boundaries(segment, reviewed_start, reviewed_end):
        if (reviewed_start is None) != (reviewed_end is None):
            raise SelectionValidationError("Provide both reviewed boundaries or neither.")
        if reviewed_start is None:
            return
        try:
            reviewed_start = float(reviewed_start)
            reviewed_end = float(reviewed_end)
        except (TypeError, ValueError) as exc:
            raise SelectionValidationError("Reviewed boundaries must be numeric.") from exc
        if reviewed_start < segment.start_seconds or reviewed_end > segment.end_seconds:
            raise SelectionValidationError("Reviewed boundaries must remain within the segment.")
        if reviewed_end < reviewed_start:
            raise SelectionValidationError("Reviewed end must not precede reviewed start.")

    @classmethod
    def select(
        cls,
        project,
        segment,
        user,
        reviewed_start=None,
        reviewed_end=None,
        notes="",
    ):
        with transaction.atomic():
            project = VideoProject.objects.select_for_update().get(pk=project.pk)
            cls.ensure_selection_allowed(project, user)
            segment = AnalysisSegment.objects.select_related(
                "analysis_run__source_video__project"
            ).get(pk=segment.pk)
            if segment.analysis_run.source_video.project_id != project.pk:
                raise SelectionValidationError("The segment does not belong to this project.")
            if segment.analysis_run.status != AnalysisRun.Status.SUCCEEDED:
                raise SelectionValidationError("Only successful analysis segments may be selected.")
            if AnalysisService.is_stale(segment.analysis_run):
                raise SelectionValidationError("This analysis is stale and must be rerun.")
            conflicting_run = SegmentSelection.objects.filter(
                project=project,
                analysis_segment__analysis_run__source_video=segment.analysis_run.source_video,
            ).exclude(analysis_segment__analysis_run=segment.analysis_run)
            if conflicting_run.exists():
                raise SelectionValidationError(
                    "Clear selections from the previous run for this source before selecting a new run."
                )
            cls._validate_reviewed_boundaries(segment, reviewed_start, reviewed_end)
            existing = SegmentSelection.objects.filter(
                project=project,
                analysis_segment=segment,
            ).first()
            if existing:
                existing.reviewed_start_seconds = reviewed_start
                existing.reviewed_end_seconds = reviewed_end
                existing.notes = notes
                existing.selected_by = user
                existing.save(
                    update_fields=[
                        "reviewed_start_seconds",
                        "reviewed_end_seconds",
                        "notes",
                        "selected_by",
                        "updated_at",
                    ]
                )
                from production.services.clips import SourceClipService

                SourceClipService.invalidate_selection_outputs(existing, user)
                return existing, False
            next_order = (
                SegmentSelection.objects.filter(project=project).aggregate(latest=Max("order"))[
                    "latest"
                ]
                or 0
            ) + 1
            selection = SegmentSelection.objects.create(
                project=project,
                analysis_segment=segment,
                order=next_order,
                reviewed_start_seconds=reviewed_start,
                reviewed_end_seconds=reviewed_end,
                notes=notes,
                selected_by=user,
            )
            return selection, True

    @classmethod
    def deselect(cls, project, selection, user):
        with transaction.atomic():
            project = VideoProject.objects.select_for_update().get(pk=project.pk)
            cls.ensure_selection_allowed(project, user)
            selection = SegmentSelection.objects.get(pk=selection.pk, project=project)
            removed_order = selection.order
            selection.delete()
            later = list(
                SegmentSelection.objects.filter(project=project, order__gt=removed_order).order_by(
                    "order"
                )
            )
            for item in later:
                item.order -= 1
                item.save(update_fields=["order", "updated_at"])

    @classmethod
    def reorder(cls, project, ordered_selection_ids, user):
        with transaction.atomic():
            project = VideoProject.objects.select_for_update().get(pk=project.pk)
            cls.ensure_selection_allowed(project, user)
            try:
                requested_ids = [int(value) for value in ordered_selection_ids]
            except (TypeError, ValueError) as exc:
                raise SelectionValidationError("Selection order contains invalid IDs.") from exc
            if len(requested_ids) != len(set(requested_ids)):
                raise SelectionValidationError("Selection order contains duplicate IDs.")
            current = list(SegmentSelection.objects.filter(project=project).order_by("order"))
            if set(requested_ids) != {item.pk for item in current}:
                raise SelectionValidationError("Selection order must contain every current selection.")
            by_id = {item.pk: item for item in current}
            offset = len(current) + 10
            for item in current:
                item.order += offset
                item.save(update_fields=["order", "updated_at"])
            reordered = []
            for order, selection_id in enumerate(requested_ids, start=1):
                item = by_id[selection_id]
                item.order = order
                item.save(update_fields=["order", "updated_at"])
                reordered.append(item)
            return reordered
