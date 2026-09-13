import json
from unittest.mock import patch
from urllib.error import HTTPError

from django.test import SimpleTestCase, override_settings

from analysis.services.providers import OpenAITranscriptAnalysisProvider, analysis_schema


def response_for(result):
    return {
        "status": "completed",
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": json.dumps(result)}],
        }],
    }


@override_settings(OPENAI_API_KEY="test-secret", OPENAI_ANALYSIS_MODEL="test-model")
class OpenAITranscriptAnalysisProviderTests(SimpleTestCase):
    @patch("analysis.services.providers.urlopen")
    def test_requests_strict_topic_schema_and_parses_output(self, open_url):
        expected = {"segments": []}
        open_url.return_value.__enter__.return_value.read.return_value = json.dumps(
            response_for(expected)
        ).encode()

        result = OpenAITranscriptAnalysisProvider().analyze(
            [{"sequence": 1, "start_seconds": 0, "duration_seconds": 2, "text": "Topic one"}],
            {"fallback_max_chunks": 20},
        )

        self.assertEqual(result, expected)
        request = open_url.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body["model"], "test-model")
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["text"]["format"]["schema"], analysis_schema())
        recommendation = body["text"]["format"]["schema"]["properties"]["segments"]["items"]["properties"]["editorial_recommendation"]
        self.assertIn("primary_approach", recommendation["required"])
        self.assertIn("pass", recommendation["properties"]["primary_approach"]["enum"])
        self.assertNotIn("test-secret", request.data.decode())
        self.assertEqual(open_url.call_args.kwargs["timeout"], 180)

    @patch("analysis.services.providers.urlopen")
    def test_provider_errors_do_not_expose_remote_details(self, open_url):
        for code, expected in ((401, "authentication"), (429, "quota"), (500, "provider returned")):
            with self.subTest(code=code):
                open_url.side_effect = HTTPError("https://example.com", code, "secret detail", {}, None)
                with self.assertRaisesRegex(RuntimeError, expected) as error:
                    OpenAITranscriptAnalysisProvider().analyze([], {})
                self.assertNotIn("secret detail", str(error.exception))

    @patch("analysis.services.providers.urlopen")
    def test_incomplete_refused_and_malformed_responses_are_safe(self, open_url):
        cases = (
            b"not json",
            json.dumps({"status": "incomplete"}).encode(),
            json.dumps({
                "status": "completed",
                "output": [{"type": "message", "role": "assistant", "content": [
                    {"type": "refusal", "refusal": "sensitive detail"}
                ]}],
            }).encode(),
        )
        for raw in cases:
            with self.subTest(raw=raw[:30]):
                open_url.side_effect = None
                open_url.return_value.__enter__.return_value.read.return_value = raw
                with self.assertRaisesRegex(RuntimeError, "incomplete or invalid") as error:
                    OpenAITranscriptAnalysisProvider().analyze([], {})
                self.assertNotIn("sensitive detail", str(error.exception))
