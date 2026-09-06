from decimal import Decimal
from hashlib import sha256

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from production.models import MediaAsset, PipelineJob, SourceClip
from production.tests.helpers import create_selected_segment, create_source_upload


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class EditorialTestCase(TestCase):
    """Keep every editorial fixture upload out of the user's media directory."""


def create_approved_clip(username="editorial-owner"):
    user, project, source, selection = create_selected_segment(username)
    source_asset = create_source_upload(project, user)
    source_asset.checksum_sha256 = sha256(b"source-video").hexdigest()
    source_asset.save(update_fields=["checksum_sha256"])
    processed = MediaAsset.objects.create(
        project=project,
        kind=MediaAsset.Kind.SOURCE_CLIP,
        status=MediaAsset.Status.APPROVED,
        display_name="Approved source clip",
        file=f"projects/{project.pk}/source_clip/clip.mp4",
        checksum_sha256=sha256(b"editorial-fixture").hexdigest(),
        byte_size=len(b"editorial-fixture"),
        duration_seconds=Decimal("2.000"),
        width=320,
        height=180,
        frame_rate=Decimal("30"),
        has_audio=True,
        has_video=True,
        detected_mime_type="video/mp4",
        created_by=user,
        validated_at=timezone.now(),
        approved_at=timezone.now(),
    )
    job = PipelineJob.objects.create(
        project=project,
        job_type="clip_trim",
        status=PipelineJob.Status.SUCCEEDED,
        progress=100,
        idempotency_key="d" * 64,
        requested_by=user,
        attempt_count=1,
        input_fingerprint="e" * 64,
        started_at=timezone.now(),
        completed_at=timezone.now(),
    )
    clip = SourceClip.objects.create(
        project=project,
        selected_segment=selection,
        source_asset=source_asset,
        processed_asset=processed,
        pipeline_job=job,
        version=1,
        status=SourceClip.Status.APPROVED,
        input_fingerprint="f" * 64,
        requested_start_seconds=Decimal("1"),
        requested_end_seconds=Decimal("3"),
        actual_start_seconds=Decimal("1"),
        actual_end_seconds=Decimal("3"),
        expected_duration_seconds=Decimal("2"),
        actual_duration_seconds=Decimal("2"),
        validated_at=timezone.now(),
        created_by=user,
        approved_by=user,
        approved_at=timezone.now(),
    )
    processed.file.save("fixture.mp4", ContentFile(b"editorial-fixture"), save=True)
    clip.refresh_from_db()
    from production.services.clips import SourceClipService

    clip.input_fingerprint = SourceClipService.current_input_fingerprint(clip)
    clip.save(update_fields=["input_fingerprint"])
    return user, project, source, selection, clip


def create_ready_package(username="editorial-owner"):
    user, project, source, selection, clip = create_approved_clip(username)
    from editorial.services.research import ResearchService

    package = ResearchService.create_package(
        clip, user,
        research_question="What is the strongest support for this point?",
        editorial_focus="Separate the transcript claim from commentary.",
    )
    package = ResearchService.mark_ready(package, user)
    return user, project, source, selection, clip, package
