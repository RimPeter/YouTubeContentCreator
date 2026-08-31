from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase, override_settings

from .models import ScrapedVideo, TranscriptEntry


class ScrapedVideoModelTests(TestCase):
	def test_entry_sequence_is_unique_per_video(self):
		scraped_video = ScrapedVideo.objects.create(
			youtube_video_id='dQw4w9WgXcQ',
			youtube_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
			video_title='Test Video',
		)
		TranscriptEntry.objects.create(
			scraped_video=scraped_video,
			sequence=1,
			start_seconds=0,
			duration_seconds=2,
			text='First entry',
		)

		with self.assertRaises(IntegrityError):
			TranscriptEntry.objects.create(
				scraped_video=scraped_video,
				sequence=1,
				start_seconds=2,
				duration_seconds=2,
				text='Duplicate entry',
			)

	def test_deleting_video_deletes_entries(self):
		scraped_video = ScrapedVideo.objects.create(
			youtube_video_id='dQw4w9WgXcQ',
			youtube_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
			video_title='Test Video',
		)
		entry = TranscriptEntry.objects.create(
			scraped_video=scraped_video,
			sequence=1,
			start_seconds=0,
			duration_seconds=2,
			text='First entry',
		)

		scraped_video.delete()

		self.assertFalse(TranscriptEntry.objects.filter(pk=entry.pk).exists())


@override_settings(ALLOWED_HOSTS=['testserver'])
class ScraperPageTests(TestCase):
	def setUp(self):
		self.scraped_video = ScrapedVideo.objects.create(
			youtube_video_id='dQw4w9WgXcQ',
			youtube_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
			video_title='Test Video',
		)
		TranscriptEntry.objects.create(
			scraped_video=self.scraped_video,
			sequence=1,
			start_seconds=0,
			duration_seconds=2,
			text='A saved transcript entry',
		)

	def test_saved_video_list_and_detail_render(self):
		list_response = self.client.get('/scraper/list/')
		detail_response = self.client.get(f'/scraper/{self.scraped_video.pk}/')

		self.assertContains(list_response, self.scraped_video.video_title)
		self.assertContains(detail_response, 'A saved transcript entry')

	@patch('scraper.views.call_command')
	def test_duplicate_submission_does_not_run_command(self, mock_call_command):
		response = self.client.post(
			'/scraper/fetch/',
			data='{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}',
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 409)
		mock_call_command.assert_not_called()

	def test_delete_transcript_removes_video_and_entries(self):
		response = self.client.post(f'/scraper/{self.scraped_video.pk}/delete/')

		self.assertRedirects(response, '/scraper/list/')
		self.assertFalse(ScrapedVideo.objects.filter(pk=self.scraped_video.pk).exists())
		self.assertFalse(TranscriptEntry.objects.filter(scraped_video_id=self.scraped_video.pk).exists())


class FetchTranscriptCommandTests(TestCase):
	@patch('scraper.management.commands.fetch_transcript.Command.fetch_video_title', return_value='Fetched Video Title')
	@patch('scraper.management.commands.fetch_transcript.YouTubeTranscriptApi')
	def test_command_saves_video_and_entries(self, mock_api_class, mock_fetch_title):
		mock_api_class.return_value.fetch.return_value = [
			type('Entry', (), {'start': 0.0, 'duration': 2.0, 'text': 'First'})(),
			type('Entry', (), {'start': 2.0, 'duration': 3.0, 'text': 'Second'})(),
		]

		from django.core.management import call_command
		call_command('fetch_transcript', '--url', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

		scraped_video = ScrapedVideo.objects.get(youtube_video_id='dQw4w9WgXcQ')
		self.assertEqual(scraped_video.video_title, 'Fetched Video Title')
		self.assertEqual(scraped_video.entries.count(), 2)
		self.assertEqual(scraped_video.entries.get(sequence=2).text, 'Second')
