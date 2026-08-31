from django.core.management.base import BaseCommand
from django.conf import settings

from scraper.models import SourceVideo
from scraper.services import TranscriptService, TranscriptExtractionError, TranscriptFetchError, TranscriptStorageError


class Command(BaseCommand):
    """
    Management command to fetch and save YouTube transcripts.
    
    Usage:
        # Fetch using URL from scraper/youtube_url.txt
        python manage.py fetch_transcript
        
        # Fetch from specific URL
        python manage.py fetch_transcript --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        
        # Fetch from project
        python manage.py fetch_transcript --project-id 1
        
        # Also export to JSON for debugging
        python manage.py fetch_transcript --url "..." --export-json
    """
    
    help = 'Fetch timestamped transcript from a YouTube video URL and save to database.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            type=str,
            help='YouTube URL to fetch transcript from'
        )
        parser.add_argument(
            '--project-id',
            type=int,
            help='Project ID to fetch transcript for'
        )
        parser.add_argument(
            '--export-json',
            action='store_true',
            help='Also export transcript to JSON file for debugging'
        )

    def handle(self, *args, **options):
        # Determine which source video to process
        source_video = None
        url = options.get('url')
        project_id = options.get('project_id')
        
        if project_id:
            # Process existing project
            try:
                source_video = SourceVideo.objects.get(project_id=project_id)
                url = source_video.youtube_url
            except SourceVideo.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Project with ID {project_id} has no source video'))
                return
        elif url:
            # Create temporary source video (in real usage, this would be from a project)
            # For now, just use the URL
            pass
        else:
            # Read from file
            try:
                url_file = settings.BASE_DIR / 'scraper' / 'youtube_url.txt'
                url = url_file.read_text().strip()
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Failed to read URL file: {e}'))
                return
        
        if not url:
            self.stdout.write(self.style.ERROR('No URL provided'))
            return
        
        # Extract video ID and log it
        try:
            video_id = TranscriptService.extract_video_id(url)
            self.stdout.write(f"Video ID: {video_id}")
        except TranscriptExtractionError as e:
            self.stdout.write(self.style.ERROR(f'Video ID extraction failed: {e}'))
            return
        
        # Fetch transcript
        try:
            self.stdout.write("Fetching transcript from YouTube API...")
            transcript = TranscriptService.fetch_transcript(video_id)
            self.stdout.write(f"✓ Fetched {len(transcript)} transcript entries")
        except TranscriptFetchError as e:
            self.stdout.write(self.style.ERROR(f'Transcript fetch failed: {e}'))
            return
        
        # If we have a source_video, save to database
        if source_video:
            try:
                self.stdout.write("Saving transcript to database...")
                chunk_count = TranscriptService.save_to_database(source_video, transcript)
                self.stdout.write(self.style.SUCCESS(f'✓ Saved {chunk_count} chunks to database'))
            except TranscriptStorageError as e:
                self.stdout.write(self.style.ERROR(f'Database save failed: {e}'))
                return
            
            # Optionally export to JSON
            if options.get('export_json'):
                try:
                    export_path = TranscriptService.export_to_json(source_video)
                    self.stdout.write(self.style.SUCCESS(f'✓ Exported to {export_path}'))
                except TranscriptStorageError as e:
                    self.stdout.write(self.style.WARNING(f'JSON export failed: {e}'))
        else:
            # Just output transcript data
            self.stdout.write("\nTranscript entries (first 5):")
            for i, entry in enumerate(transcript[:5]):
                self.stdout.write(f"  [{entry['start']:.1f}s] {entry['text'][:50]}...")
            if len(transcript) > 5:
                self.stdout.write(f"  ... and {len(transcript) - 5} more entries")

