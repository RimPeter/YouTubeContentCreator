from django.core.management.base import BaseCommand
from django.conf import settings
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
import json
from pathlib import Path
import sys


class Command(BaseCommand):
    help = 'Fetch timestamped transcript from a YouTube video URL and save it to a JSON file.'

    def add_arguments(self, parser):
        parser.add_argument('--url', type=str, help='YouTube URL to fetch transcript from')

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

    def handle(self, *args, **options):
        base_dir = settings.BASE_DIR
        url_file = base_dir / 'scraper' / 'youtube_url.txt'
        output_file = base_dir / 'scraper' / 'transcript_output.json'
        
        self.stdout.write(f"[DEBUG] BASE_DIR: {base_dir}")
        self.stdout.write(f"[DEBUG] Output file path: {output_file}")
        
        try:
            url = options.get('url') or url_file.read_text().strip()
            self.stdout.write(f"[DEBUG] URL: {url}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to read URL file: {e}'))
            return
        
        video_id = self.extract_video_id(url)
        self.stdout.write(f"[DEBUG] Video ID: {video_id}")

        if not video_id:
            self.stdout.write(self.style.ERROR('Could not extract a valid YouTube video ID from the URL.'))
            return

        try:
            self.stdout.write("[DEBUG] Fetching transcript...")
            transcript = self.fetch_timestamped_transcript(video_id)
            self.stdout.write(f"[DEBUG] Got {len(transcript)} entries")
            
            if not transcript:
                self.stdout.write(self.style.ERROR('Transcript is empty or invalid.'))
                return
            
            # Ensure directory exists
            output_file.parent.mkdir(parents=True, exist_ok=True)
            self.stdout.write(f"[DEBUG] Directory created: {output_file.parent}")
            
            # Write JSON data
            json_str = json.dumps(transcript, indent=2)
            self.stdout.write(f"[DEBUG] JSON string length: {len(json_str)} bytes")
            
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(json_str)
            
            self.stdout.write(f"[DEBUG] Write complete")
            
            # Verify file was written
            file_size = output_file.stat().st_size
            self.stdout.write(f"[DEBUG] File size after write: {file_size} bytes")
            
            if file_size == 0:
                self.stdout.write(self.style.ERROR('File was written but is empty!'))
                return
            
            self.stdout.write(self.style.SUCCESS(f'Saved {len(transcript)} timestamped transcript entries to {output_file} ({file_size} bytes)'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to fetch transcript: {e}'))
            import traceback
            traceback.print_exc(file=sys.stdout)
