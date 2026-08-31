from django.contrib import admin

from .models import ScrapedVideo, TranscriptEntry


class TranscriptEntryInline(admin.TabularInline):
	model = TranscriptEntry
	extra = 0
	fields = ('sequence', 'start_seconds', 'duration_seconds', 'text')
	readonly_fields = fields
	ordering = ('sequence',)


@admin.register(ScrapedVideo)
class ScrapedVideoAdmin(admin.ModelAdmin):
	list_display = ('video_title', 'youtube_video_id', 'entry_count', 'created_at')
	search_fields = ('video_title', 'youtube_video_id', 'youtube_url')
	readonly_fields = ('created_at', 'updated_at')
	inlines = (TranscriptEntryInline,)

	@admin.display(description='Entries')
	def entry_count(self, scraped_video):
		return scraped_video.entries.count()


@admin.register(TranscriptEntry)
class TranscriptEntryAdmin(admin.ModelAdmin):
	list_display = ('scraped_video', 'sequence', 'start_seconds', 'duration_seconds')
	list_filter = ('scraped_video',)
	search_fields = ('text', 'scraped_video__youtube_video_id')
	ordering = ('scraped_video', 'sequence')
