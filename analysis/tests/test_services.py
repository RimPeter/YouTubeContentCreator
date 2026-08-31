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
