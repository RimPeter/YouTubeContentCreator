from django.db import models


class ScrapedVideo(models.Model):
	"""A distinct YouTube video whose transcript has been saved."""

	youtube_video_id = models.CharField(max_length=11, unique=True)
	youtube_url = models.URLField()
	video_title = models.CharField(max_length=255)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return self.video_title


class TranscriptEntry(models.Model):
	"""A timestamped text entry belonging to a scraped YouTube video."""

	scraped_video = models.ForeignKey(
		ScrapedVideo,
		on_delete=models.CASCADE,
		related_name='entries',
	)
	sequence = models.PositiveIntegerField()
	start_seconds = models.FloatField()
	duration_seconds = models.FloatField()
	text = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['scraped_video', 'sequence']
		constraints = [
			models.UniqueConstraint(
				fields=['scraped_video', 'sequence'],
				name='unique_transcript_entry_sequence',
			)
		]

	def __str__(self):
		return f"{self.scraped_video.youtube_video_id} #{self.sequence}"
