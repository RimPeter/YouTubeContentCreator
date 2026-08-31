from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from production.models import MediaAsset
from production.services.probe import MediaMetadata
from scraper.models import VideoProject


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
