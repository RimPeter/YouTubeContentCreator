from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils import timezone

from analysis.models import AnalysisRun, AnalysisSegment, SegmentSelection
from analysis.services.fingerprinting import fingerprint_source_video
from production.models import MediaAsset
from production.services.probe import MediaMetadata
from scraper.models import SourceVideo, TranscriptChunk, VideoProject


def create_approved_project(username="production-owner"):
    user = get_user_model().objects.create_user(username=username, password="password")
    project = VideoProject.objects.create(
        owner=user,
        title=f"{username} project",
        status=VideoProject.Status.APPROVED,
        approved_at=timezone.now(),
    )
    return user, project


class FakeProbeClient:
    def __init__(self, metadata=None, error=None):
        self.metadata = metadata or MediaMetadata(
            duration_seconds=Decimal("12.500"),
            width=1920,
            height=1080,
            frame_rate=Decimal("30"),
            has_audio=True,
            has_video=True,
            detected_mime_type="video/mp4",
            container="mov,mp4",
            video_codec="h264",
            audio_codec="aac",
        )
        self.error = error
        self.calls = []

    def probe(self, path, extension):
        self.calls.append((path, extension))
        if self.error:
            raise self.error
        return self.metadata


def create_validated_asset(project, user, *, display_name="Asset", version=1, lineage_id=None):
    values = {
        "project": project,
        "version": version,
        "kind": MediaAsset.Kind.SOURCE_UPLOAD,
        "status": MediaAsset.Status.VALIDATED,
        "display_name": display_name,
        "file": f"projects/{project.pk}/source_upload/test-{display_name}.mp4",
        "checksum_sha256": "a" * 64,
        "byte_size": 10,
        "has_video": True,
        "detected_mime_type": "video/mp4",
        "container": "mp4",
        "created_by": user,
        "validated_at": timezone.now(),
    }
    if lineage_id:
        values["lineage_id"] = lineage_id
    return MediaAsset.objects.create(**values)


def create_selected_segment(username="clip-owner", start=1, end=3):
    user, project = create_approved_project(username)
    source = SourceVideo.objects.create(
        project=project,
        youtube_url="https://youtu.be/dQw4w9WgXcQ",
        youtube_video_id="dQw4w9WgXcQ",
        title="Clip transcript source",
        transcript_status=SourceVideo.TranscriptStatus.COMPLETED,
    )
    first = TranscriptChunk.objects.create(
        source_video=source,
        sequence=1,
        start_seconds=0,
        duration_seconds=2,
        text="First clip chunk",
    )
    second = TranscriptChunk.objects.create(
        source_video=source,
        sequence=2,
        start_seconds=2,
        duration_seconds=2,
        text="Second clip chunk",
    )
    run = AnalysisRun.objects.create(
        source_video=source,
        version=1,
        status=AnalysisRun.Status.SUCCEEDED,
        source_fingerprint=fingerprint_source_video(source),
        algorithm_version="test-v1",
        created_by=user,
        completed_at=timezone.now(),
    )
    segment = AnalysisSegment.objects.create(
        analysis_run=run,
        order=1,
        start_chunk=first,
        end_chunk=second,
        start_seconds=0,
        end_seconds=4,
        title="Selected clip segment",
        summary="Test segment",
        source_text="First clip chunk Second clip chunk",
        component_scores={},
        aggregate_score=80,
        rationale="Test",
    )
    selection = SegmentSelection.objects.create(
        project=project,
        analysis_segment=segment,
        order=1,
        reviewed_start_seconds=start,
        reviewed_end_seconds=end,
        selected_by=user,
    )
    return user, project, source, selection


def create_source_upload(project, user, *, duration="5.000", content=b"source-video"):
    asset = MediaAsset(
        project=project,
        kind=MediaAsset.Kind.SOURCE_UPLOAD,
        status=MediaAsset.Status.VALIDATED,
        display_name="Authorized source.mp4",
        checksum_sha256="b" * 64,
        byte_size=len(content),
        duration_seconds=duration,
        width=320,
        height=180,
        frame_rate=30,
        has_audio=True,
        has_video=True,
        detected_mime_type="video/mp4",
        container="mov,mp4",
        video_codec="h264",
        audio_codec="aac",
        rights_basis=MediaAsset.RightsBasis.USER_OWNED,
        rights_notes="Created by the test owner.",
        consent_metadata={"rights_confirmed": True},
        created_by=user,
        validated_at=timezone.now(),
    )
    asset.file.save("source.mp4", ContentFile(content), save=False)
    asset.save()
    return asset
