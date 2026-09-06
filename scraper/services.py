from dataclasses import dataclass
from urllib.request import urlopen
import json
import re
import math
from urllib.parse import parse_qs, urlparse

from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone
from youtube_transcript_api import YouTubeTranscriptApi

from .models import SourceVideo, TranscriptChunk, VideoProject


VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


class TranscriptServiceError(Exception):
    """Base class for transcript ingestion failures."""


class InvalidVideoInputError(TranscriptServiceError):
    pass


class DuplicateSourceError(TranscriptServiceError):
    def __init__(self, source_video):
        self.source_video = source_video
        super().__init__("This video is already attached to the project.")


class TranscriptUnavailableError(TranscriptServiceError):
    pass


class TranscriptRemoteError(TranscriptServiceError):
    pass


class ProjectMutationForbiddenError(TranscriptServiceError):
    pass


class ProjectWorkflowError(Exception):
    pass


def lock_project(project_id):
    """Serialize project writes, including on SQLite, before reading current state.

    Call as the first database operation in an atomic block. A harmless UPDATE
    obtains a database write lock on SQLite and a row lock on PostgreSQL; a
    SELECT FOR UPDATE by itself would not protect SQLite workflows.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("lock_project requires transaction.atomic().")
    if not VideoProject.objects.filter(pk=project_id).update(updated_at=F("updated_at")):
        raise VideoProject.DoesNotExist("The project no longer exists.")
    return VideoProject.objects.get(pk=project_id)


@dataclass(frozen=True)
class TranscriptIngestionResult:
    source_video: SourceVideo
    chunks_created: int
    title_fallback_used: bool


class YouTubeMetadataClient:
    def fetch_title(self, url):
        endpoint = f"https://www.youtube.com/oembed?url={url}&format=json"
        with urlopen(endpoint, timeout=10) as response:
            title = json.load(response).get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("YouTube returned no usable title.")
        return title.strip()


class TranscriptService:
    YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}

    def __init__(self, transcript_client=None, metadata_client=None):
        self.transcript_client = transcript_client or YouTubeTranscriptApi()
        self.metadata_client = metadata_client or YouTubeMetadataClient()

    @classmethod
    def normalize_video_id(cls, value):
        if not isinstance(value, str):
            raise InvalidVideoInputError("Enter a valid YouTube URL or video ID.")
        candidate = (value or "").strip()
        if VIDEO_ID_PATTERN.fullmatch(candidate):
            return candidate
        try:
            parsed = urlparse(candidate)
        except (TypeError, ValueError) as exc:
            raise InvalidVideoInputError("Enter a valid YouTube URL or video ID.") from exc
        if parsed.scheme not in {"http", "https"}:
            raise InvalidVideoInputError("Enter a valid YouTube URL or video ID.")
        host = (parsed.hostname or "").lower()
        video_id = None
        if host in cls.YOUTUBE_HOSTS:
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [None])[0]
            elif parsed.path.startswith("/shorts/"):
                video_id = parsed.path[len("/shorts/"):].split("/", 1)[0]
            elif parsed.path.startswith("/embed/"):
                video_id = parsed.path[len("/embed/"):].split("/", 1)[0]
        elif host == "youtu.be":
            video_id = parsed.path.strip("/").split("/", 1)[0]
        if not VIDEO_ID_PATTERN.fullmatch(video_id or ""):
            raise InvalidVideoInputError("Enter a valid YouTube URL or video ID.")
        return video_id

    @staticmethod
    def canonical_url(video_id):
        return f"https://www.youtube.com/watch?v={video_id}"

    @staticmethod
    def _normalize_entries(fetched_transcript):
        entries = []
        for position, entry in enumerate(fetched_transcript, start=1):
            if isinstance(entry, dict):
                start = entry.get("start")
                duration = entry.get("duration")
                text = entry.get("text")
            else:
                start = getattr(entry, "start", None)
                duration = getattr(entry, "duration", None)
                text = getattr(entry, "text", None)
            try:
                start = float(start)
                duration = float(duration)
            except (TypeError, ValueError) as exc:
                raise TranscriptRemoteError(
                    f"Transcript entry {position} contains invalid timing."
                ) from exc
            if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration < 0 or not isinstance(text, str):
                raise TranscriptRemoteError(
                    f"Transcript entry {position} contains invalid data."
                )
            entries.append(
                {
                    "sequence": position,
                    "start_seconds": start,
                    "duration_seconds": duration,
                    "text": text,
                }
            )
        if not entries:
            raise TranscriptUnavailableError("No transcript is available for this video.")
        return entries

    def ingest(self, project, submitted_url_or_id, user=None):
        try:
            project = VideoProject.objects.get(pk=project.pk)
            ProjectWorkflowService.ensure_user_access(project, user)
        except (VideoProject.DoesNotExist, ProjectWorkflowError) as exc:
            raise ProjectMutationForbiddenError(str(exc)) from exc
        if not project.content_is_mutable:
            raise ProjectMutationForbiddenError(
                "Approved, locked, or archived projects cannot accept transcript changes."
            )
        video_id = self.normalize_video_id(submitted_url_or_id)
        existing = SourceVideo.objects.filter(
            project=project,
            youtube_video_id=video_id,
        ).first()
        if existing:
            raise DuplicateSourceError(existing)

        try:
            fetched = self.transcript_client.fetch(video_id)
        except TranscriptServiceError:
            raise
        except Exception as exc:
            raise TranscriptRemoteError("YouTube transcript retrieval failed.") from exc
        entries = self._normalize_entries(fetched)

        submitted = (submitted_url_or_id or "").strip()
        source_url = submitted if submitted.startswith(("http://", "https://")) else self.canonical_url(video_id)
        title_fallback_used = False
        try:
            title = self.metadata_client.fetch_title(source_url)
            if not isinstance(title, str) or not title.strip():
                raise ValueError("No title returned")
            title = title.strip()
        except Exception:
            title = video_id
            title_fallback_used = True

        try:
            with transaction.atomic():
                try:
                    project = lock_project(project.pk)
                    ProjectWorkflowService.ensure_user_access(project, user)
                    ProjectWorkflowService.ensure_content_mutable(project)
                except (VideoProject.DoesNotExist, ProjectWorkflowError) as exc:
                    raise ProjectMutationForbiddenError(str(exc)) from exc
                source_video = SourceVideo.objects.create(
                    project=project,
                    youtube_url=source_url,
                    youtube_video_id=video_id,
                    title=title,
                    transcript_status=SourceVideo.TranscriptStatus.COMPLETED,
                )
                TranscriptChunk.objects.bulk_create(
                    [TranscriptChunk(source_video=source_video, **entry) for entry in entries]
                )
                incomplete_sources = project.source_videos.exclude(
                    transcript_status=SourceVideo.TranscriptStatus.COMPLETED
                ).exists()
                empty_sources = project.source_videos.filter(
                    transcript_chunks__isnull=True
                ).exists()
                if (
                    project.status in {VideoProject.Status.DRAFT, VideoProject.Status.FAILED}
                    and not incomplete_sources
                    and not empty_sources
                ):
                    VideoProject.objects.filter(pk=project.pk).update(
                        status=VideoProject.Status.TRANSCRIPT_READY
                    )
                    project.status = VideoProject.Status.TRANSCRIPT_READY
        except IntegrityError as exc:
            existing = SourceVideo.objects.filter(
                project=project,
                youtube_video_id=video_id,
            ).first()
            if existing:
                raise DuplicateSourceError(existing) from exc
            raise

        return TranscriptIngestionResult(
            source_video=source_video,
            chunks_created=len(entries),
            title_fallback_used=title_fallback_used,
        )


class ProjectWorkflowService:
    @staticmethod
    def ensure_user_access(project, user):
        # None is reserved for trusted management-command callers.
        if user is not None and (not user.is_authenticated or (
            project.owner_id != user.pk and not user.is_staff and not user.is_superuser
        )):
            raise ProjectWorkflowError("You cannot modify this project.")

    @classmethod
    def _current(cls, project, user=None):
        try:
            current = lock_project(getattr(project, "pk", project))
        except VideoProject.DoesNotExist as exc:
            raise ProjectWorkflowError("The project no longer exists.") from exc
        cls.ensure_user_access(current, user)
        return current

    @classmethod
    def update(cls, project, changes, user=None):
        with transaction.atomic():
            project = cls._current(project, user)
            cls.ensure_content_mutable(project)
            fields = {"title", "description", "expected_duration"}
            if set(changes) - fields:
                raise ProjectWorkflowError("Only project details may be edited.")
            for field, value in changes.items():
                setattr(project, field, value)
            project.full_clean()
            project.save(update_fields=[*changes, "updated_at"])
            return project

    @classmethod
    def delete(cls, project, user=None):
        with transaction.atomic():
            project = cls._current(project, user)
            cls.ensure_deletable(project)
            project.delete()

    @classmethod
    def discard_empty_draft(cls, project):
        """Clean up a failed command's draft only if nobody has started using it."""
        with transaction.atomic():
            try:
                project = lock_project(project.pk)
            except VideoProject.DoesNotExist:
                return False
            if project.status != VideoProject.Status.DRAFT or project.is_locked or project.source_videos.exists():
                return False
            project.delete()
            return True

    @classmethod
    def delete_source(cls, source, user=None):
        with transaction.atomic():
            project = cls._current(source.project_id, user)
            cls.ensure_deletable(project)
            try:
                source = SourceVideo.objects.get(pk=source.pk, project=project)
            except SourceVideo.DoesNotExist as exc:
                raise ProjectWorkflowError("The source no longer exists.") from exc
            source.delete()
            if project.status == VideoProject.Status.TRANSCRIPT_READY and not project.source_videos.exists():
                project.status = VideoProject.Status.DRAFT
                project.save(update_fields=["status", "updated_at"])

    @staticmethod
    def ensure_content_mutable(project):
        if not project.content_is_mutable:
            raise ProjectWorkflowError(
                "Approved, locked, or archived project content cannot be changed."
            )

    @classmethod
    def approve(cls, project, user=None):
        with transaction.atomic():
            project = cls._current(project, user)
            if project.is_locked or project.status != VideoProject.Status.TRANSCRIPT_READY:
                raise ProjectWorkflowError("Only an unlocked transcript-ready project can be approved.")
            if not project.source_videos.exists():
                raise ProjectWorkflowError("A project must contain at least one source before approval.")
            if project.source_videos.exclude(
                transcript_status=SourceVideo.TranscriptStatus.COMPLETED
            ).exists() or project.source_videos.filter(transcript_chunks__isnull=True).exists():
                raise ProjectWorkflowError("Every source must have a completed transcript before approval.")
            project.status = VideoProject.Status.APPROVED
            project.approved_at = timezone.now()
            project.save(update_fields=["status", "approved_at", "updated_at"])
            return project

    @classmethod
    def lock(cls, project, user=None):
        with transaction.atomic():
            project = cls._current(project, user)
            if project.status != VideoProject.Status.APPROVED or project.is_locked:
                raise ProjectWorkflowError("Only an unlocked approved project can be locked.")
            project.is_locked = True
            project.locked_at = timezone.now()
            project.save(update_fields=["is_locked", "locked_at", "updated_at"])
            return project

    @classmethod
    def unlock(cls, project, user=None):
        with transaction.atomic():
            project = cls._current(project, user)
            if project.status != VideoProject.Status.APPROVED or not project.is_locked:
                raise ProjectWorkflowError("Only a locked approved project can be unlocked.")
            project.is_locked = False
            project.locked_at = None
            project.save(update_fields=["is_locked", "locked_at", "updated_at"])
            return project

    @classmethod
    def archive(cls, project, user=None):
        with transaction.atomic():
            project = cls._current(project, user)
            if project.status == VideoProject.Status.ARCHIVED:
                raise ProjectWorkflowError("The project is already archived.")
            if project.is_locked:
                raise ProjectWorkflowError("Unlock the project before archiving it.")
            project.status = VideoProject.Status.ARCHIVED
            project.archived_at = timezone.now()
            project.save(update_fields=["status", "archived_at", "updated_at"])
            return project

    @classmethod
    def ensure_deletable(cls, project):
        if project.status in {VideoProject.Status.APPROVED, VideoProject.Status.ARCHIVED} or project.is_locked:
            raise ProjectWorkflowError("Approved, locked, or archived content cannot be deleted.")
