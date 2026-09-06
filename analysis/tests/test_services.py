from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from analysis.models import AnalysisRun, AnalysisSegment, SegmentSelection
from analysis.services import (
    AnalysisInputError,
    AnalysisPermissionError,
    AnalysisPersistenceError,
    AnalysisService,
    SelectionLifecycleError,
    SelectionPermissionError,
    SelectionService,
    SelectionValidationError,
    fingerprint_source_video,
)
from analysis.services.fingerprinting import ordered_transcript_chunks
from analysis.services.validation import (
    AnalysisOutputValidationError,
    DEFAULT_SCORE_WEIGHTS,
    validate_provider_output,
)
from scraper.models import VideoProject
from scraper.services import ProjectWorkflowService

from .helpers import FakeProvider, create_approved_source, scores, valid_provider_output


class AnalysisServiceTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, self.chunks = create_approved_source()

    def test_fingerprint_is_stable_and_changes_with_canonical_content(self):
        first = fingerprint_source_video(self.source)
        self.assertEqual(first, fingerprint_source_video(self.source))
        self.chunks[0].text = "Changed canonical content"
        self.chunks[0].save(update_fields=["text"])
        self.assertNotEqual(first, fingerprint_source_video(self.source))

    def test_valid_provider_output_persists_complete_scored_run(self):
        provider = FakeProvider([valid_provider_output()])
        run = AnalysisService(provider).analyze(self.source, self.user)
        self.assertEqual(run.status, AnalysisRun.Status.SUCCEEDED)
        self.assertFalse(run.used_fallback)
        self.assertEqual(run.provider, "fake-provider")
        self.assertEqual(run.segments.count(), 2)
        self.assertEqual(
            list(run.segments.values_list("start_chunk__sequence", "end_chunk__sequence")),
            [(1, 2), (3, 4)],
        )
        self.assertEqual(str(run.segments.get(order=1).aggregate_score), "80.000")
        self.assertEqual(provider.calls, 1)

    def test_malformed_provider_retries_then_uses_deterministic_fallback(self):
        malformed = {"segments": [{"start_sequence": 2, "end_sequence": 4}]}
        provider = FakeProvider([malformed, RuntimeError("provider secret")])
        run = AnalysisService(provider).analyze(
            self.source,
            self.user,
            {"fallback_max_chunks": 2, "max_provider_attempts": 2},
        )
        self.assertTrue(run.used_fallback)
        self.assertEqual(run.error_code, "provider_fallback")
        self.assertNotIn("provider secret", run.error_message)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(run.segments.count(), 2)

    def test_no_provider_uses_fallback_without_provider_error(self):
        run = AnalysisService().analyze(
            self.source,
            self.user,
            {"fallback_max_chunks": 3, "max_provider_attempts": 1},
        )
        self.assertTrue(run.used_fallback)
        self.assertEqual(run.error_code, "")
        self.assertEqual(run.segments.count(), 2)

    def test_successful_provider_retry_does_not_claim_fallback(self):
        provider = FakeProvider([RuntimeError("temporary failure"), valid_provider_output()])
        run = AnalysisService(provider).analyze(self.source, self.user)
        self.assertEqual(provider.calls, 2)
        self.assertFalse(run.used_fallback)
        self.assertEqual(run.error_code, "")
        self.assertEqual(run.error_message, "")

    def test_archive_during_provider_call_prevents_publishing_segments(self):
        provider = FakeProvider([valid_provider_output()])
        with patch.object(provider, "analyze", side_effect=lambda *_args: (ProjectWorkflowService.archive(self.project), valid_provider_output())[1]):
            with self.assertRaises(AnalysisInputError):
                AnalysisService(provider).analyze(self.source, self.user)
        run = AnalysisRun.objects.get()
        self.assertEqual(run.status, AnalysisRun.Status.FAILED)
        self.assertEqual(run.error_code, "input_changed")
        self.assertFalse(run.segments.exists())

    def test_transcript_changed_during_provider_call_prevents_publishing_segments(self):
        provider = FakeProvider([valid_provider_output()])

        def change_transcript(*_args):
            self.source.transcript_chunks.filter(pk=self.chunks[0].pk).update(text="Changed")
            return valid_provider_output()

        with patch.object(provider, "analyze", side_effect=change_transcript):
            with self.assertRaises(AnalysisInputError):
                AnalysisService(provider).analyze(self.source, self.user)
        run = AnalysisRun.objects.get()
        self.assertEqual(run.status, AnalysisRun.Status.FAILED)
        self.assertFalse(run.segments.exists())

    def test_persistence_failure_rolls_back_segments_and_marks_run_failed(self):
        provider = FakeProvider([valid_provider_output()])
        with patch.object(
            AnalysisSegment.objects,
            "bulk_create",
            side_effect=RuntimeError("database detail"),
        ):
            with self.assertRaises(AnalysisPersistenceError):
                AnalysisService(provider).analyze(self.source, self.user)
        run = AnalysisRun.objects.get()
        self.assertEqual(run.status, AnalysisRun.Status.FAILED)
        self.assertEqual(run.error_code, "persistence_failed")
        self.assertNotIn("database detail", run.error_message)
        self.assertFalse(AnalysisSegment.objects.exists())

    def test_reanalysis_versions_and_preserves_previous_run(self):
        first = AnalysisService().analyze(self.source, self.user)
        second = AnalysisService().analyze(self.source, self.user)
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertEqual(AnalysisRun.objects.count(), 2)
        self.assertTrue(first.segments.exists())

    def test_lifecycle_and_configuration_validation_happen_before_run_creation(self):
        other = get_user_model().objects.create_user(username="analysis-service-other")
        with self.assertRaises(AnalysisPermissionError):
            AnalysisService().analyze(self.source, other)
        self.project.status = VideoProject.Status.TRANSCRIPT_READY
        self.project.approved_at = None
        self.project.save(update_fields=["status", "approved_at"])
        with self.assertRaises(AnalysisInputError):
            AnalysisService().analyze(self.source, self.user)
        self.project.status = VideoProject.Status.APPROVED
        self.project.approved_at = self.project.created_at
        self.project.save(update_fields=["status", "approved_at"])
        with self.assertRaises(AnalysisInputError):
            AnalysisService().analyze(
                self.source,
                self.user,
                {"fallback_max_chunks": 0},
            )
        self.assertFalse(AnalysisRun.objects.exists())

    def test_structured_validation_rejects_gaps_overlaps_and_bad_scores(self):
        chunks = ordered_transcript_chunks(self.source)
        cases = [
            {
                "segments": [
                    {
                        "start_sequence": 2,
                        "end_sequence": 4,
                        "title": "Gap",
                        "summary": "Gap",
                        "scores": scores(),
                        "rationale": "Gap",
                    }
                ]
            },
            {
                "segments": [
                    {
                        "start_sequence": 1,
                        "end_sequence": 3,
                        "title": "One",
                        "summary": "One",
                        "scores": scores(),
                        "rationale": "One",
                    },
                    {
                        "start_sequence": 3,
                        "end_sequence": 4,
                        "title": "Overlap",
                        "summary": "Overlap",
                        "scores": scores(),
                        "rationale": "Overlap",
                    },
                ]
            },
            {
                "segments": [
                    {
                        "start_sequence": 1,
                        "end_sequence": 4,
                        "title": "Bad score",
                        "summary": "Bad score",
                        "scores": {**scores(), "clarity": 101},
                        "rationale": "Bad score",
                    }
                ]
            },
        ]
        for output in cases:
            with self.subTest(output=output), self.assertRaises(AnalysisOutputValidationError):
                validate_provider_output(output, chunks, DEFAULT_SCORE_WEIGHTS)


class SelectionServiceTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, self.chunks = create_approved_source(
            username="selection-owner"
        )
        self.run = AnalysisService(FakeProvider([valid_provider_output()])).analyze(
            self.source, self.user
        )
        self.first, self.second = list(self.run.segments.order_by("order"))

    def test_select_reorder_update_and_deselect(self):
        first_selection, created = SelectionService.select(
            self.project,
            self.first,
            self.user,
            reviewed_start=0.25,
            reviewed_end=3.5,
            notes="Use the strongest claim.",
        )
        self.assertTrue(created)
        second_selection, _ = SelectionService.select(
            self.project, self.second, self.user
        )
        SelectionService.reorder(
            self.project,
            [second_selection.pk, first_selection.pk],
            self.user,
        )
        self.assertEqual(
            list(self.project.segment_selections.values_list("pk", flat=True)),
            [second_selection.pk, first_selection.pk],
        )
        updated, created = SelectionService.select(
            self.project,
            self.first,
            self.user,
            notes="Updated note",
        )
        self.assertFalse(created)
        self.assertEqual(updated.notes, "Updated note")
        SelectionService.deselect(self.project, second_selection, self.user)
        first_selection.refresh_from_db()
        self.assertEqual(first_selection.order, 1)

    def test_permission_lifecycle_stale_and_cross_run_guards(self):
        other = get_user_model().objects.create_user(username="selection-other")
        with self.assertRaises(SelectionPermissionError):
            SelectionService.select(self.project, self.first, other)
        self.project.is_locked = True
        self.project.locked_at = self.project.approved_at
        self.project.save(update_fields=["is_locked", "locked_at"])
        with self.assertRaises(SelectionLifecycleError):
            SelectionService.select(self.project, self.first, self.user)
        self.project.is_locked = False
        self.project.locked_at = None
        self.project.save(update_fields=["is_locked", "locked_at"])

        SelectionService.select(self.project, self.first, self.user)
        second_run = AnalysisService(FakeProvider([valid_provider_output()])).analyze(
            self.source, self.user
        )
        with self.assertRaises(SelectionValidationError):
            SelectionService.select(
                self.project,
                second_run.segments.get(order=1),
                self.user,
            )

        self.chunks[0].text = "Changed after analysis"
        self.chunks[0].save(update_fields=["text"])
        with self.assertRaises(SelectionValidationError):
            SelectionService.select(self.project, self.second, self.user)

    def test_retirement_preserves_protected_clip_history_and_allows_reselection(self):
        from production.models import MediaAsset, PipelineJob, SourceClip
        from production.services.clips import SourceClipService

        selected, _ = SelectionService.select(self.project, self.first, self.user)
        source_asset = MediaAsset.objects.create(project=self.project, kind=MediaAsset.Kind.SOURCE_UPLOAD, status=MediaAsset.Status.VALIDATED, display_name="Source", created_by=self.user)
        job = PipelineJob.objects.create(project=self.project, job_type="clip_trim", status=PipelineJob.Status.SUCCEEDED, idempotency_key="a" * 64, input_fingerprint="b" * 64, requested_by=self.user)
        clip = SourceClip.objects.create(
            project=self.project, selected_segment=selected, source_asset=source_asset,
            pipeline_job=job, version=1, status=SourceClip.Status.VALIDATED,
            input_fingerprint="c" * 64, requested_start_seconds=0, requested_end_seconds=4,
            expected_duration_seconds=4, created_by=self.user,
        )
        clip.refresh_from_db()
        clip.input_fingerprint = SourceClipService.current_input_fingerprint(clip)
        clip.save(update_fields=["input_fingerprint"])
        SelectionService.deselect(self.project, selected, self.user)
        selected.refresh_from_db()
        clip.refresh_from_db()
        self.assertIsNotNone(selected.retired_at)
        self.assertEqual(selected.retired_by, self.user)
        self.assertEqual(clip.selected_segment_id, selected.pk)
        self.assertEqual(clip.status, SourceClip.Status.STALE)
        fresh, created = SelectionService.select(self.project, self.first, self.user)
        self.assertTrue(created)
        self.assertNotEqual(fresh.pk, selected.pk)
        self.assertEqual(fresh.order, 1)
        self.assertEqual(SegmentSelection.objects.count(), 2)
        self.assertEqual(list(SegmentSelection.objects.active()), [fresh])

    def test_retired_selections_do_not_block_new_run_or_reordering(self):
        first, _ = SelectionService.select(self.project, self.first, self.user)
        second, _ = SelectionService.select(self.project, self.second, self.user)
        SelectionService.deselect(self.project, first, self.user)
        SelectionService.reorder(self.project, [second.pk], self.user)
        SelectionService.deselect(self.project, second, self.user)
        next_run = AnalysisService(FakeProvider([valid_provider_output()])).analyze(self.source, self.user)
        next_selection, created = SelectionService.select(self.project, next_run.segments.first(), self.user)
        self.assertTrue(created)
        self.assertEqual(next_selection.order, 1)
        self.assertEqual(SegmentSelection.objects.count(), 3)
        with self.assertRaises(SelectionValidationError):
            SelectionService.deselect(self.project, first, self.user)
        with self.assertRaises(SelectionValidationError):
            SelectionService.reorder(self.project, [first.pk, next_selection.pk], self.user)
