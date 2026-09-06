from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from YoutubeContent.local_settings import read_local_ai_settings


class LocalAISettingsTests(SimpleTestCase):
    def test_accepts_env_style_and_quoted_values_without_executing_code(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "env.py"
            path.write_text(
                '# Local settings\nOPENAI_API_KEY=test-key\nOPENAI_RESEARCH_MODEL="test-model"\n'
                'OTHER_SECRET=ignored\nraise RuntimeError("must not run")\n', encoding="utf-8",
            )
            self.assertEqual(read_local_ai_settings(path), {
                "OPENAI_API_KEY": "test-key", "OPENAI_RESEARCH_MODEL": "test-model",
            })

    def test_missing_file_and_invalid_values_are_ignored(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "env.py"
            self.assertEqual(read_local_ai_settings(path), {})
            path.write_text('OPENAI_API_KEY=\nOPENAI_RESEARCH_MODEL="unterminated\n', encoding="utf-8")
            self.assertEqual(read_local_ai_settings(path), {})
