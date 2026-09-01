from django.contrib import admin

from .models import EvidenceSource, ReactionBlock, ReactionClaim, ResearchPackage


class EvidenceInline(admin.TabularInline):
    model = EvidenceSource
    extra = 0
    readonly_fields = ("verification_status", "verified_by", "verified_at")


@admin.register(ResearchPackage)
class ResearchPackageAdmin(admin.ModelAdmin):
    list_display = ("source_clip", "version", "status", "created_by", "reviewed_by")
    list_filter = ("status",)
    inlines = (EvidenceInline,)


class ClaimInline(admin.TabularInline):
    model = ReactionClaim
    extra = 0


@admin.register(ReactionBlock)
class ReactionBlockAdmin(admin.ModelAdmin):
    list_display = ("source_clip", "version", "status", "reaction_type", "approved_by")
    list_filter = ("status", "reaction_type", "used_fallback")
    inlines = (ClaimInline,)


@admin.register(EvidenceSource)
class EvidenceSourceAdmin(admin.ModelAdmin):
    list_display = ("title", "research_package", "classification", "verification_status")
    list_filter = ("classification", "verification_status", "source_origin")


@admin.register(ReactionClaim)
class ReactionClaimAdmin(admin.ModelAdmin):
    list_display = ("reaction_block", "order", "claim_type", "transcript_chunk", "evidence_source")
    list_filter = ("claim_type",)
