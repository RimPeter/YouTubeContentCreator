import math
from pathlib import Path
from django.db.models import Max
from django.utils import timezone

from editorial.models import (ReactionProductionAssembly, ReactionProductionAssemblyItem,
                              ReactionTimelineItem, TimelineNarrationTake)
from production.models import MediaAsset, SourceClip
from production.services.fingerprints import fingerprint_json
from production.services.media_assets import MediaAssetService
from production.services.narration import NarrationProbe
from production.services.clips import SourceClipService

from .access import EditorialServiceError, editorial_transaction
from .reaction_timelines import ReactionTimelineService


class ReactionProductionService:
    @staticmethod
    def review_fingerprint(timeline):
        return fingerprint_json(list(timeline.items.filter(included=True).values(
            "id", "order", "item_type", "label", "transcript_text", "source_start_seconds", "source_end_seconds"
        )))

    @classmethod
    def script_issues(cls, timeline):
        issues = []
        if ReactionTimelineService.is_stale(timeline):
            issues.append("Upstream inputs changed; create a current timeline.")
        items = list(timeline.items.filter(included=True).select_related("plan_section__selection__analysis_segment"))
        if not any(i.item_type == "creator" for i in items):
            issues.append("Include at least one creator turn.")
        for item in items:
            if not item.transcript_text.strip():
                issues.append(f"Turn #{item.order} has empty text.")
            if item.item_type == "source":
                selection = item.plan_section.selection
                segment = selection.analysis_segment
                lower = selection.reviewed_start_seconds if selection.reviewed_start_seconds is not None else segment.start_seconds
                upper = selection.reviewed_end_seconds if selection.reviewed_end_seconds is not None else segment.end_seconds
                start, end = item.source_start_seconds, item.source_end_seconds
                if start is None or end is None or not (math.isfinite(start) and math.isfinite(end) and lower <= start < end <= upper):
                    issues.append(f"Source turn #{item.order} has an invalid trim.")
        return issues

    @classmethod
    def review(cls, timeline, user):
        with editorial_transaction(timeline.project_id, user):
            timeline = ReactionTimelineService._current_timeline(timeline, user)
            issues = cls.script_issues(timeline)
            if issues:
                raise EditorialServiceError(" ".join(issues))
            timeline.reviewed_fingerprint = cls.review_fingerprint(timeline)
            timeline.reviewed_by = user
            timeline.reviewed_at = timezone.now()
            timeline.status = timeline.Status.RECORDING
            timeline.save(update_fields=["reviewed_fingerprint", "reviewed_by", "reviewed_at", "status"])
            return timeline

    @classmethod
    def require_review(cls, timeline):
        if cls.script_issues(timeline) or timeline.reviewed_fingerprint != cls.review_fingerprint(timeline):
            raise EditorialServiceError("Review the current script and mark it ready for recording first.")

    @staticmethod
    def available(asset):
        return bool(asset and asset.file and asset.checksum_sha256 and asset.duration_seconds
                    and asset.duration_seconds > 0 and asset.status in {"validated", "approved"}
                    and asset.file.storage.exists(asset.file.name))

    @staticmethod
    def script_fingerprint(item):
        return fingerprint_json({"timeline_item": item.pk, "text": item.transcript_text})

    @classmethod
    def upload_take(cls, item, uploaded_file, user, *, rights_basis, rights_notes="", rights_confirmed=False,
                    probe_client=None, recording_notes=""):
        if Path(uploaded_file.name).suffix.lower() not in {".wav", ".mp3", ".m4a", ".aac"}:
            raise EditorialServiceError("Upload WAV, MP3, M4A or AAC audio.")
        if item.item_type != ReactionTimelineItem.ItemType.CREATOR:
            raise EditorialServiceError("Narration can only be attached to a creator transcript turn.")
        if not rights_confirmed or rights_basis not in {"user_owned", "licensed", "permission", "public_domain"}:
            raise EditorialServiceError("Confirm your authorization to use this recording.")
        with editorial_transaction(item.timeline.project_id, user):
            timeline = ReactionTimelineService._current_timeline(item.timeline, user)
            cls.require_review(timeline)
            item = ReactionTimelineItem.objects.select_for_update().get(pk=item.pk, timeline=timeline)
            if not item.included:
                raise EditorialServiceError("Include the creator turn before uploading narration.")
            fingerprint = cls.script_fingerprint(item)
        asset = MediaAssetService(NarrationProbe(probe_client)).create_upload(
            timeline.project, uploaded_file, user, kind=MediaAsset.Kind.NARRATION,
            rights_basis=rights_basis, rights_notes=rights_notes,
            consent_metadata={"rights_confirmed": True, "confirmed_by": user.pk},
            configuration={"purpose": "reaction_timeline_narration", "timeline_item": item.pk},
        )
        try:
            with editorial_transaction(item.timeline.project_id, user):
                timeline = ReactionTimelineService._current_timeline(timeline, user)
                cls.require_review(timeline)
                item = ReactionTimelineItem.objects.select_for_update().get(pk=item.pk, timeline=timeline)
                if not item.included or cls.script_fingerprint(item) != fingerprint:
                    raise EditorialServiceError("The creator script changed during upload; record the current text.")
                version = (item.narration_takes.aggregate(latest=Max("version"))["latest"] or 0) + 1
                return TimelineNarrationTake.objects.create(
                    timeline_item=item, media_asset=asset, version=version, script_text=item.transcript_text,
                    script_fingerprint=fingerprint, created_by=user, recording_notes=recording_notes[:5000],
                )
        except Exception:
            MediaAsset.objects.filter(pk=asset.pk).update(status="failed", error_code="timeline_upload_changed",
                error_message="Recording could not be attached to the current script.")
            raise

    @classmethod
    def approve_take(cls, take, user, *, select_only=False):
        with editorial_transaction(take.timeline_item.timeline.project_id, user):
            take = TimelineNarrationTake.objects.select_for_update().select_related(
                "timeline_item__timeline", "media_asset"
            ).get(pk=take.pk)
            ReactionTimelineService._current_timeline(take.timeline_item.timeline, user)
            cls.require_review(take.timeline_item.timeline)
            if not take.timeline_item.included or not cls.available(take.media_asset) or not take.media_asset.has_audio or take.media_asset.has_video:
                raise EditorialServiceError("This turn or recording is unavailable.")
            if take.script_fingerprint != cls.script_fingerprint(take.timeline_item):
                raise EditorialServiceError("This take belongs to an older creator script.")
            if select_only and take.approved_at is None:
                raise EditorialServiceError("Approve this take before selecting it.")
            if not select_only:
                MediaAsset.objects.filter(pk=take.media_asset_id).update(status=MediaAsset.Status.APPROVED, approved_at=timezone.now())
            if not select_only:
                take.approved_by = user
                take.approved_at = timezone.now()
                take.save(update_fields=["approved_by", "approved_at"])
            TimelineNarrationTake.objects.filter(timeline_item=take.timeline_item, selected=True).update(selected=False)
            take.selected = True
            take.save(update_fields=["selected"])
            from .crud import audit
            audit(take.timeline_item.timeline.project_id, user, "narration_selected" if select_only else "narration_approved", {"take": take.pk})
            cls.sync_timeline_status(take.timeline_item.timeline, persist=True)
            return take

    @classmethod
    def readiness(cls, timeline):
        items = list(timeline.items.filter(included=True).select_related("plan_section__selection"))
        issues, resolved = cls.script_issues(timeline), []
        if timeline.reviewed_fingerprint != cls.review_fingerprint(timeline):
            issues.append("Review the current script before recording or handoff.")
        for item in items:
            if item.item_type == ReactionTimelineItem.ItemType.CREATOR:
                take = item.narration_takes.select_related("media_asset").filter(
                    selected=True, approved_at__isnull=False, script_fingerprint=cls.script_fingerprint(item),
                    media_asset__status=MediaAsset.Status.APPROVED,
                ).order_by("-version").first()
                if take and not cls.available(take.media_asset):
                    take = None
                if not take:
                    issues.append(f"Creator turn #{item.order} needs an approved current narration take.")
                resolved.append((item, take, None))
            else:
                clip = SourceClip.objects.filter(
                    selected_segment=item.plan_section.selection, status=SourceClip.Status.APPROVED,
                ).select_related("processed_asset").first()
                if clip and (SourceClipService.is_stale(clip) or not cls.available(clip.processed_asset)
                             or not clip.processed_asset.has_video
                             or clip.actual_start_seconds is None or clip.actual_end_seconds is None
                             or item.source_start_seconds is None or item.source_end_seconds is None
                             or not (float(clip.actual_start_seconds) <= item.source_start_seconds < item.source_end_seconds <= float(clip.actual_end_seconds))):
                    clip = None
                if not clip or not clip.processed_asset_id:
                    issues.append(f"Source turn #{item.order} needs an approved source clip.")
                resolved.append((item, None, clip))
        if not items:
            issues.append("Include at least one timeline turn.")
        return issues, resolved

    @classmethod
    def sync_timeline_status(cls, timeline, *, persist=False):
        if ReactionTimelineService.is_stale(timeline):
            status = timeline.Status.STALE
        else:
            issues, resolved = cls.readiness(timeline)
            if not issues:
                status = timeline.Status.READY
            elif timeline.reviewed_fingerprint == cls.review_fingerprint(timeline) and not cls.script_issues(timeline):
                status = timeline.Status.RECORDING
            else:
                status = timeline.Status.DRAFT
        if timeline.status != status:
            timeline.status = status
            if persist:
                timeline.save(update_fields=["status"])
        return status

    @classmethod
    def assembly_fingerprint(cls, timeline, resolved):
        return fingerprint_json({"timeline": timeline.pk, "items": [cls.snapshot(item, take, clip) for item, take, clip in resolved]})

    @staticmethod
    def snapshot(item, take, clip):
        asset = take.media_asset if take else clip.processed_asset
        return {"item": item.pk, "order": item.order, "type": item.item_type, "label": item.label,
                "text": item.transcript_text, "start": item.source_start_seconds, "end": item.source_end_seconds,
                "take": take.pk if take else None, "clip": clip.pk if clip else None,
                "media": asset.pk, "file": asset.file.name, "checksum": asset.checksum_sha256,
                "duration": float(asset.duration_seconds) if take else item.source_end_seconds - item.source_start_seconds,
                "media_in": 0 if take else item.source_start_seconds - float(clip.actual_start_seconds)}

    @classmethod
    def create_assembly(cls, timeline, user):
        with editorial_transaction(timeline.project_id, user):
            timeline = ReactionTimelineService._current_timeline(timeline, user)
            issues, resolved = cls.readiness(timeline)
            if issues:
                cls.sync_timeline_status(timeline)
                raise EditorialServiceError(" ".join(issues))
            version = (timeline.production_assemblies.aggregate(latest=Max("version"))["latest"] or 0) + 1
            assembly = ReactionProductionAssembly.objects.create(
                timeline=timeline, version=version, status=ReactionProductionAssembly.Status.READY,
                input_fingerprint=cls.assembly_fingerprint(timeline, resolved), created_by=user,
            )
            ReactionProductionAssemblyItem.objects.bulk_create([
                ReactionProductionAssemblyItem(
                    assembly=assembly, order=index, timeline_item=item, narration_take=take, source_clip=clip,
                    transcript_text=item.transcript_text, source_start_seconds=item.source_start_seconds,
                    source_end_seconds=item.source_end_seconds,
                    snapshot=cls.snapshot(item, take, clip),
                ) for index, (item, take, clip) in enumerate(resolved, 1)
            ])
            cls.sync_timeline_status(timeline, persist=True)
            return assembly

    @classmethod
    def is_assembly_stale(cls, assembly):
        if assembly.status == "stale" or assembly.items.filter(snapshot={}).exists():
            return True
        assembly.timeline.refresh_from_db()
        issues, resolved = cls.readiness(assembly.timeline)
        return bool(issues) or assembly.input_fingerprint != cls.assembly_fingerprint(assembly.timeline, resolved)
