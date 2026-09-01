from django.contrib import admin

from .models import ArtifactDependency, MediaAsset, PipelineJob, SourceClip


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "project",
        "kind",
        "version",
        "status",
        "byte_size",
        "updated_at",
    )
    list_filter = ("kind", "status", "origin", "rights_basis")
    search_fields = ("display_name", "project__title", "checksum_sha256")
    readonly_fields = (
        "lineage_id",
        "checksum_sha256",
        "byte_size",
        "duration_seconds",
        "width",
        "height",
        "frame_rate",
        "has_audio",
        "has_video",
        "detected_mime_type",
        "container",
        "video_codec",
        "audio_codec",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
        "validated_at",
        "approved_at",
    )


@admin.register(PipelineJob)
class PipelineJobAdmin(admin.ModelAdmin):
    list_display = (
        "job_type",
        "project",
        "status",
        "progress",
        "attempt_count",
        "max_attempts",
        "created_at",
    )
    list_filter = ("job_type", "status")
    search_fields = ("project__title", "idempotency_key", "input_fingerprint")
    readonly_fields = (
        "idempotency_key",
        "input_fingerprint",
        "input_snapshot",
        "configuration_snapshot",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "cancelled_at",
    )


@admin.register(SourceClip)
class SourceClipAdmin(admin.ModelAdmin):
    list_display = (
        "selected_segment",
        "project",
        "version",
        "status",
        "actual_duration_seconds",
        "updated_at",
    )
    list_filter = ("status",)
    search_fields = (
        "project__title",
        "selected_segment__analysis_segment__title",
        "input_fingerprint",
    )
    readonly_fields = (
        "input_fingerprint",
        "validation_result",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
        "validated_at",
        "approved_at",
    )


@admin.register(ArtifactDependency)
class ArtifactDependencyAdmin(admin.ModelAdmin):
    list_display = (
        "project",
        "upstream_content_type",
        "upstream_object_id",
        "upstream_version",
        "downstream_content_type",
        "downstream_object_id",
        "downstream_version",
        "relation_type",
    )
    list_filter = ("relation_type", "upstream_content_type", "downstream_content_type")
    search_fields = ("project__title", "upstream_fingerprint")
    readonly_fields = (
        "project",
        "upstream_content_type",
        "upstream_object_id",
        "upstream_version",
        "upstream_fingerprint",
        "downstream_content_type",
        "downstream_object_id",
        "downstream_version",
        "relation_type",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {"GET", "HEAD", "OPTIONS"}

    def has_delete_permission(self, request, obj=None):
        return False
