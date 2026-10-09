"""Readable source excerpts for analysis without an AI summary."""
import re


def fallback_excerpt(text, target_length=400, max_length=4000):
    text = " ".join(text.replace(">>", " ").split())
    if not text:
        return "No transcript text is available."
    endings = [match.end() for match in re.finditer(r'''[.!?]+["'\u2019\u201d)]*(?=\s|$)''', text)]
    within_target = [end for end in endings if end <= target_length]
    if within_target:
        return text[:within_target[-1]]
    if endings and endings[0] <= max_length:
        # A long first sentence is preferable to an arbitrary mid-sentence cut.
        return text[:endings[0]]
    # Some transcripts contain no sentence punctuation. Mark the fragment honestly
    # and never split a word to meet the persisted summary length limit.
    fragment = text[:max_length - 1]
    if len(text) > len(fragment):
        fragment = fragment.rsplit(" ", 1)[0] if " " in fragment else ""
    return fragment.rstrip() + "\u2026"
