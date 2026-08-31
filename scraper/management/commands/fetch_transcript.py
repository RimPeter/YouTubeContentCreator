from django.core.management.base import BaseCommand
from django.db import transaction
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen
import json
import sys

from scraper.models import ScrapedVideo, TranscriptEntry


class Command(BaseCommand):
    help = 'Fetch a timestamped transcript from a YouTube video URL and save it to the database.'

    def add_arguments(self, parser):
        parser.add_argument('--url', required=True, help='YouTube URL to fetch transcript from')

    def extract_video_id(self, url):
        parsed = urlparse(url)
        if parsed.netloc in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
            if parsed.path == '/watch':
                return parse_qs(parsed.query).get('v', [None])[0]
            if parsed.path.startswith('/shorts/'):
                return parsed.path.split('/shorts/')[1].split('/')[0]
            if parsed.path.startswith('/embed/'):
                return parsed.path.split('/embed/')[1].split('/')[0]
        return None

    def fetch_timestamped_transcript(self, video_id):
        transcript = YouTubeTranscriptApi().fetch(video_id)
        return [
            {
                'start': entry.start,
                'duration': entry.duration,
                'text': entry.text,
            }
            for entry in transcript
        ]

    def fetch_video_title(self, url, fallback_title):
        """Retrieve the public YouTube title without requiring an API key."""
        endpoint = f'https://www.youtube.com/oembed?url={url}&format=json'
        try:
            with urlopen(endpoint, timeout=10) as response:
                return json.load(response).get('title') or fallback_title
        except Exception:
            return fallback_title

    def handle(self, *args, **options):
        url = options['url'].strip()
        
        video_id = self.extract_video_id(url)
        if not video_id:
            self.stdout.write(self.style.ERROR('Could not extract a valid YouTube video ID from the URL.'))
            return

        if ScrapedVideo.objects.filter(youtube_video_id=video_id).exists():
            self.stdout.write(self.style.ERROR('This video has already been scraped.'))
            return

        try:
            transcript = self.fetch_timestamped_transcript(video_id)
            if not transcript:
                self.stdout.write(self.style.ERROR('Transcript is empty or invalid.'))
                return

            video_title = self.fetch_video_title(url, video_id)

            with transaction.atomic():
                scraped_video = ScrapedVideo.objects.create(
                    youtube_video_id=video_id,
                    youtube_url=url,
                    video_title=video_title,
                )
                TranscriptEntry.objects.bulk_create([
                    TranscriptEntry(
                        scraped_video=scraped_video,
                        sequence=sequence,
                        start_seconds=entry['start'],
                        duration_seconds=entry['duration'],
                        text=entry['text'],
                    )
                    for sequence, entry in enumerate(transcript, start=1)
                ])

            self.stdout.write(self.style.SUCCESS(
                f'Saved {len(transcript)} transcript entries for "{scraped_video.video_title}".'
            ))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to fetch transcript: {e}'))
            import traceback
            traceback.print_exc(file=sys.stdout)
