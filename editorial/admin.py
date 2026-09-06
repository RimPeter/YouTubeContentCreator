from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import EvidenceSource, ReactionBlock, ReactionClaim, ResearchPackage


class ReadOnlyEditorialAdmin:
    """Inspect audit records here; the editorial UI owns all mutations."""

    actions = None

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        raise PermissionDenied("Use the editorial workflow to change artifacts.")

    def save_formset(self, request, form, formset, change):
        raise PermissionDenied("Use the editorial workflow to change artifacts.")

    def delete_model(self, request, obj):
        raise PermissionDenied("Editorial history cannot be deleted from admin.")

    def delete_queryset(self, request, queryset):
        raise PermissionDenied("Editorial history cannot be deleted from admin.")


class EvidenceInline(ReadOnlyEditorialAdmin, admin.TabularInline):
    model = EvidenceSource
    extra = 0


@admin.register(ResearchPackage)
class ResearchPackageAdmin(ReadOnlyEditorialAdmin, admin.ModelAdmin):
    list_display = ("source_clip", "version", "status", "created_by", "reviewed_by")
    list_filter = ("status",)
    inlines = (EvidenceInline,)


class ClaimInline(ReadOnlyEditorialAdmin, admin.TabularInline):
    model = ReactionClaim
    extra = 0


@admin.register(ReactionBlock)
class ReactionBlockAdmin(ReadOnlyEditorialAdmin, admin.ModelAdmin):
    list_display = ("source_clip", "version", "status", "reaction_type", "approved_by")
    list_filter = ("status", "reaction_type", "used_fallback")
    inlines = (ClaimInline,)


@admin.register(EvidenceSource)
class EvidenceSourceAdmin(ReadOnlyEditorialAdmin, admin.ModelAdmin):
    list_display = ("title", "research_package", "classification", "verification_status")
    list_filter = ("classification", "verification_status", "source_origin")


@admin.register(ReactionClaim)
class ReactionClaimAdmin(ReadOnlyEditorialAdmin, admin.ModelAdmin):
    list_display = ("reaction_block", "order", "claim_type", "transcript_chunk", "evidence_source")
    list_filter = ("claim_type",)
