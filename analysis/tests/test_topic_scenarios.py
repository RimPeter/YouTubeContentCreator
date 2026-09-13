from django.test import TestCase

from analysis.services import AnalysisService

from .helpers import FakeProvider, create_approved_source
from .topic_scenarios import TOPIC_SCENARIOS, _output


class TopicSegmentationScenarioTests(TestCase):
    def test_editorial_scenarios_persist_contiguous_expected_boundaries(self):
        for scenario in TOPIC_SCENARIOS:
            with self.subTest(scenario=scenario["name"]):
                user, _project, source, chunks = create_approved_source(
                    username=f"scenario-{scenario['name']}",
                    chunk_count=len(scenario["chunks"]),
                )
                for chunk, text in zip(chunks, scenario["chunks"]):
                    chunk.text = text
                    chunk.save(update_fields=["text"])
                provider = FakeProvider([_output(scenario["boundaries"])])
                run = AnalysisService(provider).analyze(source, user)
                self.assertFalse(run.used_fallback)
                self.assertEqual(
                    list(run.segments.values_list("start_chunk__sequence", "end_chunk__sequence")),
                    [(start, end) for start, end, _title in scenario["boundaries"]],
                )
