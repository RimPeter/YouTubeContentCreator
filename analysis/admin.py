from django.contrib import admin
from scraper.admin import ReadOnlyArtifactAdmin, ReadOnlyArtifactInline

from .models import AnalysisRun, AnalysisSegment, SegmentSelection


class AnalysisSegmentInline(ReadOnlyArtifactInline):
    model = AnalysisSegment
    extra = 0
    fields = ("order", "title", "start_seconds", "end_seconds", "aggregate_score")
    readonly_fields = fields
    show_change_link = True


@admin.register(AnalysisRun)
class AnalysisRunAdmin(ReadOnlyArtifactAdmin):
    list_display = (
        "source_video",
        "version",
        "status",
        "used_fallback",
        "provider",
        "started_at",
    )
    list_filter = ("status", "used_fallback", "provider")
    search_fields = ("source_video__title", "source_video__youtube_video_id")
    readonly_fields = (
        "source_fingerprint",
        "configuration",
        "error_code",
        "error_message",
        "started_at",
        "completed_at",
    )
    inlines = (AnalysisSegmentInline,)


@admin.register(AnalysisSegment)
class AnalysisSegmentAdmin(ReadOnlyArtifactAdmin):
    list_display = ("analysis_run", "order", "title", "aggregate_score")
    ordering = ("analysis_run", "order")
    search_fields = ("title", "summary", "source_text")


@admin.register(SegmentSelection)
class SegmentSelectionAdmin(ReadOnlyArtifactAdmin):
    list_display = ("project", "order", "analysis_segment", "selected_by", "retired_at", "updated_at")
    ordering = ("project", "order")
    search_fields = ("project__title", "analysis_segment__title")
