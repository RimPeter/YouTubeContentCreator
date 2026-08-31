from django.test import TestCase
from django.utils import timezone
from .models import VideoProject, SourceVideo, TranscriptChunk


class VideoProjectModelTests(TestCase):
    """Test cases for VideoProject model."""
    
    def setUp(self):
        """Create a test project."""
        self.project = VideoProject.objects.create(
            title="Test Reaction Video",
            status='draft',
            description="A test project",
            expected_duration=600
        )
    
    def test_create_project(self):
        """Test creating a VideoProject."""
        self.assertEqual(self.project.title, "Test Reaction Video")
        self.assertEqual(self.project.status, 'draft')
        self.assertIsNotNone(self.project.created_at)
    
    def test_project_str_representation(self):
        """Test string representation of VideoProject."""
        expected = "Test Reaction Video (Draft)"
        self.assertEqual(str(self.project), expected)
    
    def test_project_status_choices(self):
        """Test all valid status choices."""
        valid_statuses = [
            'draft', 'transcript_ready', 'analysis_ready',
            'needs_review', 'approved', 'locked', 'failed'
        ]
        for status in valid_statuses:
            project = VideoProject.objects.create(
                title=f"Test {status}",
                status=status
            )
            self.assertEqual(project.status, status)
    
    def test_project_updated_at_changes(self):
        """Test that updated_at changes when project is modified."""
        original_updated = self.project.updated_at
        self.project.title = "Updated Title"
        self.project.save()
        self.assertGreater(self.project.updated_at, original_updated)
    
    def test_project_locked_status(self):
        """Test locking a project."""
        self.project.status = 'locked'
        self.project.locked_at = timezone.now()
        self.project.save()
        
        locked_project = VideoProject.objects.get(pk=self.project.pk)
        self.assertEqual(locked_project.status, 'locked')
        self.assertIsNotNone(locked_project.locked_at)


class SourceVideoModelTests(TestCase):
    """Test cases for SourceVideo model."""
    
    def setUp(self):
        """Create test project and source video."""
        self.project = VideoProject.objects.create(
            title="Test Project",
            status='draft'
        )
        self.source_video = SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="Test Video",
            channel="Test Channel",
            duration=212,
            thumbnail_url="https://img.youtube.com/vi/dQw4w9WgXcQ/default.jpg"
        )
    
    def test_create_source_video(self):
        """Test creating a SourceVideo."""
        self.assertEqual(self.source_video.title, "Test Video")
        self.assertEqual(self.source_video.youtube_video_id, "dQw4w9WgXcQ")
        self.assertEqual(self.source_video.project, self.project)
        self.assertFalse(self.source_video.transcript_fetched)
    
    def test_source_video_str_representation(self):
        """Test string representation of SourceVideo."""
        expected = "Test Video (dQw4w9WgXcQ)"
        self.assertEqual(str(self.source_video), expected)
    
    def test_source_video_one_to_one_relationship(self):
        """Test that only one SourceVideo can exist per VideoProject."""
        self.assertEqual(self.project.source_video, self.source_video)
    
    def test_source_video_transcript_status(self):
        """Test transcript status field."""
        self.assertEqual(self.source_video.transcript_status, 'pending')
        
        self.source_video.transcript_status = 'fetching'
        self.source_video.save()
        
        updated = SourceVideo.objects.get(pk=self.source_video.pk)
        self.assertEqual(updated.transcript_status, 'fetching')
    
    def test_source_video_transcript_fetched_flag(self):
        """Test marking transcript as fetched."""
        self.assertFalse(self.source_video.transcript_fetched)
        
        self.source_video.transcript_fetched = True
        self.source_video.transcript_status = 'completed'
        self.source_video.save()
        
        updated = SourceVideo.objects.get(pk=self.source_video.pk)
        self.assertTrue(updated.transcript_fetched)
        self.assertEqual(updated.transcript_status, 'completed')
    
    def test_youtube_video_id_uniqueness(self):
        """Test that youtube_video_id must be unique."""
        another_project = VideoProject.objects.create(title="Another Project")
        
        with self.assertRaises(Exception):
            # Attempting to create another source video with the same ID should fail
            SourceVideo.objects.create(
                project=another_project,
                youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                youtube_video_id="dQw4w9WgXcQ",  # Same ID
                title="Different Video",
                channel="Another Channel"
            )


class TranscriptChunkModelTests(TestCase):
    """Test cases for TranscriptChunk model."""
    
    def setUp(self):
        """Create test project, source video, and transcript chunks."""
        self.project = VideoProject.objects.create(
            title="Test Project",
            status='draft'
        )
        self.source_video = SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="Test Video",
            channel="Test Channel",
            duration=100
        )
        self.chunk = TranscriptChunk.objects.create(
            source_video=self.source_video,
            sequence=1,
            start=0.0,
            duration=5.0,
            text="This is the first transcript chunk."
        )
    
    def test_create_transcript_chunk(self):
        """Test creating a TranscriptChunk."""
        self.assertEqual(self.chunk.sequence, 1)
        self.assertEqual(self.chunk.start, 0.0)
        self.assertEqual(self.chunk.duration, 5.0)
        self.assertFalse(self.chunk.processed)
    
    def test_transcript_chunk_str_representation(self):
        """Test string representation of TranscriptChunk."""
        expected = "Chunk 1: This is the first transcript chunk...."
        self.assertEqual(str(self.chunk), expected)
    
    def test_transcript_chunk_sequence_ordering(self):
        """Test that chunks can be ordered by sequence."""
        chunk2 = TranscriptChunk.objects.create(
            source_video=self.source_video,
            sequence=2,
            start=5.0,
            duration=5.0,
            text="This is the second transcript chunk."
        )
        chunk3 = TranscriptChunk.objects.create(
            source_video=self.source_video,
            sequence=3,
            start=10.0,
            duration=5.0,
            text="This is the third transcript chunk."
        )
        
        chunks = TranscriptChunk.objects.filter(source_video=self.source_video)
        sequences = [c.sequence for c in chunks]
        self.assertEqual(sequences, [1, 2, 3])
    
    def test_transcript_chunk_segment_type(self):
        """Test segment type field."""
        self.chunk.segment_type = 'intro'
        self.chunk.save()
        
        updated = TranscriptChunk.objects.get(pk=self.chunk.pk)
        self.assertEqual(updated.segment_type, 'intro')
    
    def test_transcript_chunk_processed_status(self):
        """Test marking chunk as processed."""
        self.assertFalse(self.chunk.processed)
        
        self.chunk.processed = True
        self.chunk.segment_type = 'reaction'
        self.chunk.save()
        
        updated = TranscriptChunk.objects.get(pk=self.chunk.pk)
        self.assertTrue(updated.processed)
        self.assertEqual(updated.segment_type, 'reaction')
    
    def test_unique_sequence_per_video(self):
        """Test that sequence must be unique per source_video."""
        with self.assertRaises(Exception):
            # Attempting to create another chunk with the same sequence should fail
            TranscriptChunk.objects.create(
                source_video=self.source_video,
                sequence=1,  # Same sequence
                start=5.0,
                duration=5.0,
                text="Duplicate sequence"
            )
    
    def test_transcript_chunks_cascade_delete(self):
        """Test that chunks are deleted when source video is deleted."""
        chunk_id = self.chunk.pk
        self.source_video.delete()
        
        with self.assertRaises(TranscriptChunk.DoesNotExist):
            TranscriptChunk.objects.get(pk=chunk_id)


class ModelIntegrationTests(TestCase):
    """Integration tests for model relationships."""
    
    def setUp(self):
        """Create a complete project structure."""
        self.project = VideoProject.objects.create(
            title="Complete Test Project",
            status='draft'
        )
        self.source_video = SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://www.youtube.com/watch?v=test123",
            youtube_video_id="test123",
            title="Test Video",
            channel="Test Channel"
        )
        # Create multiple chunks
        for i in range(1, 4):
            TranscriptChunk.objects.create(
                source_video=self.source_video,
                sequence=i,
                start=(i-1)*5.0,
                duration=5.0,
                text=f"Chunk {i} text"
            )
    
    def test_project_source_video_relationship(self):
        """Test the project-source_video one-to-one relationship."""
        self.assertEqual(self.project.source_video, self.source_video)
        self.assertEqual(self.source_video.project, self.project)
    
    def test_source_video_chunks_relationship(self):
        """Test the source_video-chunks one-to-many relationship."""
        chunks = self.source_video.transcript_chunks.all()
        self.assertEqual(chunks.count(), 3)
    
    def test_full_project_workflow(self):
        """Test a complete workflow: create -> fetch -> process."""
        # Initial state
        self.assertEqual(self.project.status, 'draft')
        self.assertFalse(self.source_video.transcript_fetched)
        
        # Mark as transcript ready
        self.source_video.transcript_fetched = True
        self.source_video.transcript_status = 'completed'
        self.source_video.save()
        
        self.project.status = 'transcript_ready'
        self.project.current_pipeline_stage = 'transcript_fetched'
        self.project.save()
        
        # Process chunks
        for chunk in self.source_video.transcript_chunks.all():
            chunk.processed = True
            chunk.segment_type = 'reaction'
            chunk.save()
        
        # Mark as analysis ready
        self.project.status = 'analysis_ready'
        self.project.save()
        
        # Verify final state
        updated_project = VideoProject.objects.get(pk=self.project.pk)
        updated_source = SourceVideo.objects.get(pk=self.source_video.pk)
        processed_chunks = TranscriptChunk.objects.filter(
            source_video=updated_source,
            processed=True
        )
        
        self.assertEqual(updated_project.status, 'analysis_ready')
        self.assertTrue(updated_source.transcript_fetched)
        self.assertEqual(processed_chunks.count(), 3)


class TranscriptServiceTests(TestCase):
    """Test cases for TranscriptService."""
    
    def setUp(self):
        """Create a test project and source video."""
        self.project = VideoProject.objects.create(
            title="Service Test Project",
            status='draft'
        )
        self.source_video = SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            youtube_video_id="dQw4w9WgXcQ",
            title="Test Video",
            channel="Test Channel"
        )
    
    def test_extract_video_id_watch_format(self):
        """Test extracting video ID from watch?v=ID format."""
        from .services import TranscriptService
        
        urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        ]
        
        for url in urls:
            video_id = TranscriptService.extract_video_id(url)
            self.assertEqual(video_id, "dQw4w9WgXcQ")
    
    def test_extract_video_id_shorts_format(self):
        """Test extracting video ID from /shorts/ID format."""
        from .services import TranscriptService
        
        url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        video_id = TranscriptService.extract_video_id(url)
        self.assertEqual(video_id, "dQw4w9WgXcQ")
    
    def test_extract_video_id_embed_format(self):
        """Test extracting video ID from /embed/ID format."""
        from .services import TranscriptService
        
        url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        video_id = TranscriptService.extract_video_id(url)
        self.assertEqual(video_id, "dQw4w9WgXcQ")
    
    def test_extract_video_id_youtu_be_format(self):
        """Test extracting video ID from youtu.be short URL format."""
        from .services import TranscriptService
        
        url = "https://youtu.be/dQw4w9WgXcQ"
        video_id = TranscriptService.extract_video_id(url)
        self.assertEqual(video_id, "dQw4w9WgXcQ")
    
    def test_extract_video_id_invalid_url(self):
        """Test that invalid URLs raise an error."""
        from .services import TranscriptService, TranscriptExtractionError
        
        invalid_urls = [
            "https://example.com/watch?v=invalid",
            "not a url",
            "https://youtube.com/invalid",
        ]
        
        for url in invalid_urls:
            with self.assertRaises(TranscriptExtractionError):
                TranscriptService.extract_video_id(url)
    
    def test_save_to_database(self):
        """Test saving transcript chunks to database."""
        from .services import TranscriptService
        
        transcript = [
            {'start': 0.0, 'duration': 5.0, 'text': 'First chunk'},
            {'start': 5.0, 'duration': 5.0, 'text': 'Second chunk'},
            {'start': 10.0, 'duration': 5.0, 'text': 'Third chunk'},
        ]
        
        chunk_count = TranscriptService.save_to_database(self.source_video, transcript)
        
        self.assertEqual(chunk_count, 3)
        self.assertTrue(self.source_video.transcript_fetched)
        self.assertEqual(self.source_video.transcript_status, 'completed')
        
        # Verify chunks were saved
        chunks = self.source_video.transcript_chunks.all()
        self.assertEqual(chunks.count(), 3)
        
        # Verify chunk data
        first_chunk = chunks.get(sequence=1)
        self.assertEqual(first_chunk.text, 'First chunk')
        self.assertEqual(first_chunk.start, 0.0)
    
    def test_save_to_database_clears_existing(self):
        """Test that saving new transcript clears old chunks."""
        from .services import TranscriptService
        
        # Create initial chunks
        transcript1 = [
            {'start': 0.0, 'duration': 5.0, 'text': 'Old chunk 1'},
            {'start': 5.0, 'duration': 5.0, 'text': 'Old chunk 2'},
        ]
        TranscriptService.save_to_database(self.source_video, transcript1)
        self.assertEqual(self.source_video.transcript_chunks.count(), 2)
        
        # Save new transcript
        transcript2 = [
            {'start': 0.0, 'duration': 5.0, 'text': 'New chunk'},
        ]
        TranscriptService.save_to_database(self.source_video, transcript2)
        
        # Old chunks should be deleted
        self.assertEqual(self.source_video.transcript_chunks.count(), 1)
        self.assertEqual(
            self.source_video.transcript_chunks.first().text,
            'New chunk'
        )
    
    def test_export_to_json(self):
        """Test exporting transcript to JSON file."""
        from .services import TranscriptService
        import tempfile
        import json
        
        # Create transcript
        transcript = [
            {'start': 0.0, 'duration': 5.0, 'text': 'First chunk'},
            {'start': 5.0, 'duration': 5.0, 'text': 'Second chunk'},
        ]
        TranscriptService.save_to_database(self.source_video, transcript)
        
        # Export to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            export_path = f.name
        
        from pathlib import Path
        export_path = Path(export_path)
        
        try:
            result_path = TranscriptService.export_to_json(self.source_video, export_path)
            
            # Verify file exists and contains correct data
            self.assertTrue(result_path.exists())
            
            with open(result_path, 'r') as f:
                data = json.load(f)
            
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]['text'], 'First chunk')
            self.assertEqual(data[1]['text'], 'Second chunk')
        finally:
            # Clean up
            if export_path.exists():
                export_path.unlink()
    
    def test_process_video_workflow(self):
        """Test the complete process_video workflow (without real API)."""
        from .services import TranscriptService
        from unittest.mock import patch
        
        # Mock the YouTube API call
        mock_transcript = [
            {'start': 0.0, 'duration': 3.0, 'text': 'Hello'},
            {'start': 3.0, 'duration': 3.0, 'text': 'World'},
        ]
        
        with patch.object(TranscriptService, 'fetch_transcript', return_value=mock_transcript):
            result = TranscriptService.process_video(self.source_video)
        
        self.assertTrue(result['success'])
        self.assertEqual(result['video_id'], 'dQw4w9WgXcQ')
        self.assertEqual(result['chunks_saved'], 2)
        
        # Verify state
        updated_source = SourceVideo.objects.get(pk=self.source_video.pk)
        self.assertTrue(updated_source.transcript_fetched)
        self.assertEqual(updated_source.transcript_status, 'completed')
        self.assertEqual(updated_source.transcript_chunks.count(), 2)

