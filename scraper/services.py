"""
Transcript service for fetching and storing YouTube transcripts.

This service encapsulates all transcript-related operations:
- Extracting video IDs from YouTube URLs
- Fetching transcripts from YouTube API
- Saving transcripts to the database
- Exporting transcripts for debugging
"""

import json
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Optional, List, Dict, Any

from django.conf import settings
from youtube_transcript_api import YouTubeTranscriptApi

from .models import SourceVideo, TranscriptChunk

logger = logging.getLogger(__name__)


class TranscriptExtractionError(Exception):
    """Raised when video ID extraction fails."""
    pass


class TranscriptFetchError(Exception):
    """Raised when transcript fetching fails."""
    pass


class TranscriptStorageError(Exception):
    """Raised when saving transcript to database fails."""
    pass


class TranscriptService:
    """
    Service for managing YouTube transcript operations.
    
    Responsibilities:
    - Extract video ID from YouTube URLs
    - Fetch transcripts from YouTube API
    - Save transcripts to database
    - Export transcripts for debugging
    """
    
    # Supported YouTube URL patterns
    YOUTUBE_DOMAINS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}
    
    @staticmethod
    def extract_video_id(url: str) -> str:
        """
        Extract YouTube video ID from various URL formats.
        
        Supports:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtube.com/shorts/VIDEO_ID
        - https://youtube.com/embed/VIDEO_ID
        - https://youtu.be/VIDEO_ID
        
        Args:
            url: YouTube URL
            
        Returns:
            Video ID string
            
        Raises:
            TranscriptExtractionError: If video ID cannot be extracted
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.replace('www.', '')
            
            # Handle youtu.be short URLs
            if domain == 'youtu.be':
                video_id = parsed.path.lstrip('/')
                if video_id and len(video_id) == 11:
                    return video_id
            
            # Handle youtube.com URLs
            if domain == 'youtube.com' or domain == 'm.youtube.com':
                # Handle watch?v=ID format
                if parsed.path == '/watch':
                    video_id = parse_qs(parsed.query).get('v', [None])[0]
                    if video_id:
                        return video_id
                
                # Handle /shorts/ID format
                if parsed.path.startswith('/shorts/'):
                    video_id = parsed.path.split('/shorts/')[1].split('/')[0]
                    if video_id:
                        return video_id
                
                # Handle /embed/ID format
                if parsed.path.startswith('/embed/'):
                    video_id = parsed.path.split('/embed/')[1].split('/')[0]
                    if video_id:
                        return video_id
            
            raise TranscriptExtractionError(f"Could not extract video ID from URL: {url}")
        
        except Exception as e:
            if isinstance(e, TranscriptExtractionError):
                raise
            raise TranscriptExtractionError(f"Error parsing URL: {str(e)}")
    
    @staticmethod
    def fetch_transcript(video_id: str) -> List[Dict[str, Any]]:
        """
        Fetch transcript from YouTube API.
        
        Args:
            video_id: YouTube video ID
            
        Returns:
            List of transcript entries with 'start', 'duration', 'text'
            
        Raises:
            TranscriptFetchError: If fetching fails
        """
        try:
            logger.info(f"Fetching transcript for video: {video_id}")
            
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            
            if not transcript_list:
                raise TranscriptFetchError(f"No transcript available for video: {video_id}")
            
            # Normalize to standard format
            transcript = [
                {
                    'start': entry['start'],
                    'duration': entry['duration'],
                    'text': entry['text'],
                }
                for entry in transcript_list
            ]
            
            logger.info(f"Successfully fetched {len(transcript)} transcript entries")
            return transcript
        
        except Exception as e:
            if isinstance(e, TranscriptFetchError):
                raise
            raise TranscriptFetchError(f"Failed to fetch transcript: {str(e)}")
    
    @staticmethod
    def save_to_database(
        source_video: SourceVideo,
        transcript: List[Dict[str, Any]]
    ) -> int:
        """
        Save transcript entries to database as TranscriptChunk records.
        
        Args:
            source_video: SourceVideo instance to associate chunks with
            transcript: List of transcript entries
            
        Returns:
            Number of chunks saved
            
        Raises:
            TranscriptStorageError: If saving fails
        """
        try:
            logger.info(f"Saving {len(transcript)} chunks to database for {source_video}")
            
            # Clear existing chunks
            source_video.transcript_chunks.all().delete()
            
            chunks = [
                TranscriptChunk(
                    source_video=source_video,
                    sequence=i + 1,
                    start=entry['start'],
                    duration=entry['duration'],
                    text=entry['text'],
                )
                for i, entry in enumerate(transcript)
            ]
            
            # Bulk create for efficiency
            TranscriptChunk.objects.bulk_create(chunks, batch_size=1000)
            
            # Update source video status
            source_video.transcript_fetched = True
            source_video.transcript_status = 'completed'
            source_video.save()
            
            logger.info(f"Successfully saved {len(chunks)} chunks to database")
            return len(chunks)
        
        except Exception as e:
            logger.error(f"Failed to save transcript to database: {str(e)}")
            raise TranscriptStorageError(f"Failed to save transcript: {str(e)}")
    
    @staticmethod
    def export_to_json(
        source_video: SourceVideo,
        output_path: Optional[Path] = None
    ) -> Path:
        """
        Export transcript chunks to JSON file (for debugging/backup).
        
        Args:
            source_video: SourceVideo instance to export
            output_path: Path to save JSON file (optional, defaults to scraper/transcript_output.json)
            
        Returns:
            Path to created JSON file
            
        Raises:
            TranscriptStorageError: If export fails
        """
        try:
            if output_path is None:
                output_path = settings.BASE_DIR / 'scraper' / 'transcript_output.json'
            
            logger.info(f"Exporting transcript to {output_path}")
            
            chunks = source_video.transcript_chunks.all().order_by('sequence')
            transcript_data = [
                {
                    'start': chunk.start,
                    'duration': chunk.duration,
                    'text': chunk.text,
                }
                for chunk in chunks
            ]
            
            # Ensure directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write JSON
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(transcript_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Successfully exported {len(transcript_data)} entries to {output_path}")
            return output_path
        
        except Exception as e:
            logger.error(f"Failed to export transcript to JSON: {str(e)}")
            raise TranscriptStorageError(f"Failed to export transcript: {str(e)}")
    
    @classmethod
    def process_video(
        cls,
        source_video: SourceVideo,
        export_json: bool = False
    ) -> Dict[str, Any]:
        """
        Complete workflow: fetch transcript and save to database.
        
        Args:
            source_video: SourceVideo instance to process
            export_json: Whether to also export to JSON for debugging
            
        Returns:
            Dictionary with processing results
            
        Raises:
            TranscriptExtractionError: If video ID extraction fails
            TranscriptFetchError: If fetching fails
            TranscriptStorageError: If saving fails
        """
        try:
            logger.info(f"Starting transcript processing for {source_video}")
            
            # Update status to fetching
            source_video.transcript_status = 'fetching'
            source_video.save()
            
            # Extract video ID
            video_id = cls.extract_video_id(source_video.youtube_url)
            
            # Fetch transcript
            transcript = cls.fetch_transcript(video_id)
            
            # Save to database
            chunk_count = cls.save_to_database(source_video, transcript)
            
            result = {
                'success': True,
                'video_id': video_id,
                'chunks_saved': chunk_count,
                'export_path': None,
            }
            
            # Optionally export to JSON
            if export_json:
                export_path = cls.export_to_json(source_video)
                result['export_path'] = str(export_path)
            
            logger.info(f"Successfully processed {source_video}: {chunk_count} chunks")
            return result
        
        except Exception as e:
            # Update status to failed
            source_video.transcript_status = 'failed'
            source_video.save()
            
            logger.error(f"Failed to process {source_video}: {str(e)}")
            raise
