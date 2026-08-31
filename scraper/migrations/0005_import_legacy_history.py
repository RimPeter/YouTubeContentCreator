import re
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.db import migrations


BATCH_VERSION = "scraper.0005_import_legacy_history_v1"
IMPORT_USERNAME = "__legacy_transcript_import__"
IMPORT_PROJECT_TITLE = "Imported transcript history"
IMPORT_PROJECT_DESCRIPTION = "System-owned project created by the legacy transcript migration."
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_video_id(url):
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        for prefix in ("/shorts/", "/embed/"):
            if parsed.path.startswith(prefix):
                return parsed.path[len(prefix):].split("/", 1)[0]
    if host == "youtu.be":
        return parsed.path.strip("/").split("/", 1)[0]
    return None


def record_issue(
    Conflict,
    legacy_video_id,
    severity,
    reason_code,
    detail,
    legacy_entry_id=0,
):
    Conflict.objects.update_or_create(
        batch_version=BATCH_VERSION,
        legacy_scraped_video_id=legacy_video_id,
        legacy_transcript_entry_id=legacy_entry_id,
        reason_code=reason_code,
        defaults={"severity": severity, "detail": detail},
    )


def import_legacy_history(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".", 1)
    User = apps.get_model(app_label, model_name)
    VideoProject = apps.get_model("scraper", "VideoProject")
    SourceVideo = apps.get_model("scraper", "SourceVideo")
    TranscriptChunk = apps.get_model("scraper", "TranscriptChunk")
    ScrapedVideo = apps.get_model("scraper", "ScrapedVideo")
    TranscriptEntry = apps.get_model("scraper", "TranscriptEntry")
    Conflict = apps.get_model("scraper", "LegacyImportConflict")

    import_user = User.objects.filter(username=IMPORT_USERNAME).first()
    if import_user is not None and (
        import_user.is_active or not str(import_user.password).startswith("!")
    ):
        raise RuntimeError(
            f"Reserved import username {IMPORT_USERNAME!r} belongs to an incompatible account."
        )
    if import_user is None:
        import_user = User.objects.create(
            username=IMPORT_USERNAME,
            password="!",
            is_active=False,
        )

    imported_project, _ = VideoProject.objects.get_or_create(
        owner_id=import_user.pk,
        title=IMPORT_PROJECT_TITLE,
        defaults={
            "description": IMPORT_PROJECT_DESCRIPTION,
            "status": "draft",
            "is_locked": False,
        },
    )

    for legacy_video in ScrapedVideo.objects.order_by("pk"):
        stored_video_id = legacy_video.youtube_video_id
        extracted_video_id = extract_video_id(legacy_video.youtube_url)
        identity_errors = []
        if not VIDEO_ID_PATTERN.fullmatch(stored_video_id or ""):
            identity_errors.append("invalid_stored_video_id")
        if extracted_video_id != stored_video_id:
            identity_errors.append("url_video_id_mismatch")
        if identity_errors:
            record_issue(
                Conflict,
                legacy_video.pk,
                "error",
                "invalid_video_identity",
                {
                    "errors": identity_errors,
                    "stored_video_id": stored_video_id,
                    "extracted_video_id": extracted_video_id,
                    "youtube_url": legacy_video.youtube_url,
                },
            )
            continue

        entries = list(
            TranscriptEntry.objects.filter(scraped_video_id=legacy_video.pk).order_by(
                "sequence", "pk"
            )
        )
        invalid_entries = []
        for entry in entries:
            errors = []
            if entry.sequence < 1:
                errors.append("sequence_lt_1")
            if entry.start_seconds < 0:
                errors.append("negative_start")
            if entry.duration_seconds < 0:
                errors.append("negative_duration")
            if errors:
                invalid_entries.append(entry.pk)
                record_issue(
                    Conflict,
                    legacy_video.pk,
                    "error",
                    "invalid_transcript_entry",
                    {"errors": errors, "sequence": entry.sequence},
                    legacy_entry_id=entry.pk,
                )
        if invalid_entries:
            continue

        existing_by_provenance = SourceVideo.objects.filter(
            legacy_scraped_video_id=legacy_video.pk
        ).first()
        existing_by_identity = SourceVideo.objects.filter(
            project_id=imported_project.pk,
            youtube_video_id=stored_video_id,
        ).first()
        if existing_by_provenance is not None:
            if (
                existing_by_provenance.project_id != imported_project.pk
                or existing_by_provenance.youtube_video_id != stored_video_id
            ):
                record_issue(
                    Conflict,
                    legacy_video.pk,
                    "error",
                    "provenance_collision",
                    {"canonical_source_id": existing_by_provenance.pk},
                )
            continue
        if existing_by_identity is not None:
            record_issue(
                Conflict,
                legacy_video.pk,
                "error",
                "canonical_identity_collision",
                {
                    "canonical_source_id": existing_by_identity.pk,
                    "canonical_legacy_id": existing_by_identity.legacy_scraped_video_id,
                },
            )
            continue

        title = legacy_video.video_title.strip()
        if not title:
            title = stored_video_id
            record_issue(
                Conflict,
                legacy_video.pk,
                "warning",
                "blank_title_fallback",
                {"fallback_title": title},
            )

        source_video = SourceVideo.objects.create(
            project_id=imported_project.pk,
            youtube_url=legacy_video.youtube_url,
            youtube_video_id=stored_video_id,
            title=title,
            transcript_status="completed" if entries else "pending",
            legacy_scraped_video_id=legacy_video.pk,
        )
        SourceVideo.objects.filter(pk=source_video.pk).update(
            created_at=legacy_video.created_at,
            updated_at=legacy_video.updated_at,
        )

        TranscriptChunk.objects.bulk_create(
            [
                TranscriptChunk(
                    source_video_id=source_video.pk,
                    sequence=entry.sequence,
                    start_seconds=entry.start_seconds,
                    duration_seconds=entry.duration_seconds,
                    text=entry.text,
                )
                for entry in entries
            ]
        )
        for entry in entries:
            TranscriptChunk.objects.filter(
                source_video_id=source_video.pk,
                sequence=entry.sequence,
            ).update(created_at=entry.created_at)

    imported_sources = SourceVideo.objects.filter(project_id=imported_project.pk)
    project_ready = imported_sources.exists() and not imported_sources.exclude(
        transcript_status="completed"
    ).exists()
    if project_ready:
        project_ready = not imported_sources.filter(transcript_chunks__isnull=True).exists()
    VideoProject.objects.filter(pk=imported_project.pk).update(
        status="transcript_ready" if project_ready else "draft"
    )

    imported_ids = set(
        SourceVideo.objects.filter(legacy_scraped_video_id__isnull=False).values_list(
            "legacy_scraped_video_id", flat=True
        )
    )
    errored_ids = set(
        Conflict.objects.filter(batch_version=BATCH_VERSION, severity="error").values_list(
            "legacy_scraped_video_id", flat=True
        )
    )
    unaccounted_ids = set(ScrapedVideo.objects.values_list("pk", flat=True)) - imported_ids - errored_ids
    if unaccounted_ids:
        raise RuntimeError(f"Legacy videos were not imported or recorded as conflicts: {unaccounted_ids}")


def reverse_legacy_history(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".", 1)
    User = apps.get_model(app_label, model_name)
    VideoProject = apps.get_model("scraper", "VideoProject")
    SourceVideo = apps.get_model("scraper", "SourceVideo")
    Conflict = apps.get_model("scraper", "LegacyImportConflict")

    SourceVideo.objects.filter(legacy_scraped_video_id__isnull=False).delete()
    Conflict.objects.filter(batch_version=BATCH_VERSION).delete()

    import_user = User.objects.filter(username=IMPORT_USERNAME).first()
    if import_user is None:
        return
    imported_projects = VideoProject.objects.filter(
        owner_id=import_user.pk,
        title=IMPORT_PROJECT_TITLE,
        description=IMPORT_PROJECT_DESCRIPTION,
    )
    for project in imported_projects:
        if not project.source_videos.exists():
            project.delete()
    # Retain the inactive reserved account. Deleting a swappable historical user
    # can traverse third-party relations rendered from a different app state.
    # Keeping an unusable inactive account is safer than a partially reversed
    # migration; a later operational cleanup may remove it after verification.


class Migration(migrations.Migration):
    dependencies = [("scraper", "0004_restore_canonical_schema")]

    operations = [
        migrations.RunPython(import_legacy_history, reverse_legacy_history),
    ]
