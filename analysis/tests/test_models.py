from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from analysis.models import AnalysisRun, AnalysisSegment, SegmentSelection
from scraper.models import SourceVideo, TranscriptChunk, VideoProject

from .helpers import create_approved_source, scores


class AnalysisModelTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, self.chunks = create_approved_source()
        self.run = AnalysisRun.objects.create(
            source_video=self.source,
            version=1,
            status=AnalysisRun.Status.SUCCEEDED,
            source_fingerprint="a" * 64,
            algorithm_version="test-v1",
            created_by=self.user,
            completed_at=timezone.now(),
        )
        self.segment = AnalysisSegment.objects.create(
            analysis_run=self.run,
            order=1,
            start_chunk=self.chunks[0],
            end_chunk=self.chunks[1],
            start_seconds=0,
            end_seconds=4,
            title="Segment",
            summary="Summary",
            source_text="Chunk 1 text Chunk 2 text",
            topic_labels=["topic"],
            component_scores=scores(),
            aggregate_score=Decimal("80.000"),
            rationale="Rationale",
        )

    def test_run_segment_and_selection_uniqueness_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnalysisRun.objects.create(
                source_video=self.source,
                version=1,
                source_fingerprint="b" * 64,
                algorithm_version="test-v1",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnalysisSegment.objects.create(
                analysis_run=self.run,
                order=1,
                start_chunk=self.chunks[2],
                end_chunk=self.chunks[3],
                start_seconds=4,
                end_seconds=8,
                title="Duplicate order",
                summary="Summary",
                source_text="Text",
                aggregate_score=50,
                rationale="Rationale",
            )
        SegmentSelection.objects.create(
            project=self.project,
            analysis_segment=self.segment,
            order=1,
            selected_by=self.user,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SegmentSelection.objects.create(
                project=self.project,
                analysis_segment=self.segment,
                order=2,
                selected_by=self.user,
            )

    def test_segment_validation_rejects_wrong_source_boundaries(self):
        other_source = SourceVideo.objects.create(
            project=self.project,
            youtube_url="https://youtu.be/jwXITbC9pVI",
            youtube_video_id="jwXITbC9pVI",
            title="Other",
            transcript_status=SourceVideo.TranscriptStatus.COMPLETED,
        )
        other_chunk = TranscriptChunk.objects.create(
            source_video=other_source,
            sequence=1,
            start_seconds=0,
            duration_seconds=1,
            text="Other",
        )
        invalid = AnalysisSegment(
            analysis_run=self.run,
            order=2,
            start_chunk=self.chunks[0],
            end_chunk=other_chunk,
            start_seconds=0,
            end_seconds=1,
            title="Invalid",
            summary="Invalid",
            source_text="Invalid",
            aggregate_score=50,
            rationale="Invalid",
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()

    def test_selection_validation_rejects_wrong_project_and_boundaries(self):
        other_user = get_user_model().objects.create_user(username="other-model-user")
        other_project = VideoProject.objects.create(
            owner=other_user,
            title="Other project",
            status=VideoProject.Status.APPROVED,
            approved_at=timezone.now(),
        )
        selection = SegmentSelection(
            project=other_project,
            analysis_segment=self.segment,
            order=1,
            reviewed_start_seconds=-1,
            reviewed_end_seconds=5,
        )
        with self.assertRaises(ValidationError):
            selection.full_clean()
