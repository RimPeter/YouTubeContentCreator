from django.contrib import admin

from .models import (
    LegacyImportConflict,
    SourceVideo,
    TranscriptChunk,
    VideoProject,
)


class ReadOnlyArtifactAdmin(admin.ModelAdmin):
    """Inspect workflow records; all writes go through the application services."""

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyArtifactInline(admin.TabularInline):
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SourceVideoInline(ReadOnlyArtifactInline):
    model = SourceVideo
    extra = 0
    fields = ("title", "youtube_video_id", "transcript_status", "created_at")
    readonly_fields = fields
    show_change_link = True


@admin.register(VideoProject)
class VideoProjectAdmin(ReadOnlyArtifactAdmin):
    list_display = ("title", "owner", "status", "is_locked", "updated_at")
    list_filter = ("status", "is_locked")
    search_fields = ("title", "owner__username")
    readonly_fields = ("approved_at", "archived_at", "locked_at", "created_at", "updated_at")
    inlines = (SourceVideoInline,)


class TranscriptChunkInline(ReadOnlyArtifactInline):
    model = TranscriptChunk
    extra = 0
    fields = ("sequence", "start_seconds", "duration_seconds", "text")
    readonly_fields = fields
    ordering = ("sequence",)


@admin.register(SourceVideo)
class SourceVideoAdmin(ReadOnlyArtifactAdmin):
    list_display = ("title", "project", "youtube_video_id", "transcript_status", "created_at")
    list_filter = ("transcript_status",)
    search_fields = ("title", "youtube_video_id", "project__title")
    readonly_fields = ("legacy_scraped_video_id", "created_at", "updated_at")
    inlines = (TranscriptChunkInline,)


@admin.register(TranscriptChunk)
class TranscriptChunkAdmin(ReadOnlyArtifactAdmin):
    list_display = ("source_video", "sequence", "start_seconds", "duration_seconds")
    search_fields = ("text", "source_video__youtube_video_id")
    ordering = ("source_video", "sequence")


@admin.register(LegacyImportConflict)
class LegacyImportConflictAdmin(ReadOnlyArtifactAdmin):
    list_display = (
        "legacy_scraped_video_id",
        "legacy_transcript_entry_id",
        "severity",
        "reason_code",
        "created_at",
    )
    list_filter = ("severity", "reason_code", "batch_version")
    readonly_fields = (
        "batch_version",
        "legacy_scraped_video_id",
        "legacy_transcript_entry_id",
        "severity",
        "reason_code",
        "detail",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
