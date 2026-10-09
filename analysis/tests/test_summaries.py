from django.test import SimpleTestCase

from analysis.services.summaries import fallback_excerpt
from analysis.services.validation import build_fallback_output
from types import SimpleNamespace


class FallbackExcerptTests(SimpleTestCase):
    def test_stops_at_last_complete_sentence_before_cutoff(self):
        complete = "This is a complete sentence. " * 10
        text = complete + ">> " + "An unfinished thought " * 20
        self.assertEqual(fallback_excerpt(text), complete.strip())
        output = build_fallback_output([SimpleNamespace(sequence=1, text=text)], 1)
        self.assertEqual(output["segments"][0]["summary"], complete.strip())

    def test_long_first_sentence_is_kept_whole(self):
        sentence = "A longer sentence " * 30 + "ends here."
        self.assertEqual(fallback_excerpt(sentence + " Another thought."), sentence)

    def test_unpunctuated_text_uses_word_boundary_and_ellipsis(self):
        self.assertEqual(fallback_excerpt("some words without punctuation", max_length=20), "some words without\u2026")
        self.assertEqual(fallback_excerpt("unfinished thought"), "unfinished thought\u2026")

    def test_preserves_closing_quotes_and_removes_speaker_markers(self):
        self.assertEqual(fallback_excerpt('>> She said "Done." >> Next thought'), 'She said "Done."')
