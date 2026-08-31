from django.contrib import admin
from .models import VideoProject, SourceVideo, TranscriptChunk


@admin.register(VideoProject)
class VideoProjectAdmin(admin.ModelAdmin):
    """
    Admin interface for VideoProject model.
    """
    list_display = ['title', 'status', 'current_pipeline_stage', 'updated_at', 'locked_at']
    list_filter = ['status', 'created_at', 'updated_at']
    search_fields = ['title', 'description']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Project Information', {
            'fields': ('title', 'description', 'expected_duration')
        }),
        ('Status', {
            'fields': ('status', 'current_pipeline_stage')
        }),
        ('Dates', {
            'fields': ('created_at', 'updated_at', 'approved_at', 'locked_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(SourceVideo)
class SourceVideoAdmin(admin.ModelAdmin):
    """
    Admin interface for SourceVideo model.
    """
    list_display = ['title', 'youtube_video_id', 'project', 'transcript_status', 'created_at']
    list_filter = ['transcript_status', 'created_at', 'transcript_fetched']
    search_fields = ['title', 'channel', 'youtube_url']
    readonly_fields = ['youtube_video_id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Video Information', {
            'fields': ('project', 'title', 'channel', 'duration')
        }),
        ('YouTube Details', {
            'fields': ('youtube_url', 'youtube_video_id', 'thumbnail_url')
        }),
        ('Transcript Status', {
            'fields': ('transcript_status', 'transcript_fetched')
        }),
        ('Dates', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(TranscriptChunk)
class TranscriptChunkAdmin(admin.ModelAdmin):
    """
    Admin interface for TranscriptChunk model.
    """
    list_display = ['sequence', 'source_video', 'segment_type', 'start', 'duration', 'processed']
    list_filter = ['processed', 'segment_type', 'source_video', 'created_at']
    search_fields = ['text', 'source_video__title']
    readonly_fields = ['created_at']
    
    fieldsets = (
        ('Transcript Information', {
            'fields': ('source_video', 'sequence', 'text')
        }),
        ('Timing', {
            'fields': ('start', 'duration')
        }),
        ('Processing', {
            'fields': ('processed', 'segment_type')
        }),
        ('Dates', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

