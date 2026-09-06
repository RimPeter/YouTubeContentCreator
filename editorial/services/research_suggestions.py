"""Local research prompts grounded in transcript text, without external claims."""

import re

from production.models import SourceClip
from production.services.clips import SourceClipService

from .access import EditorialServiceError, ensure_editorial_allowed


def suggest_from_transcript(text):
    text = re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]|>>", " ", text)).strip()
    if not text:
        raise EditorialServiceError("This clip has no transcript text to base suggestions on.")
    lower = text.lower()
    suggestions = []

    def add(label, question, focus):
        suggestions.append({"label": label, "research_question": question, "editorial_focus": focus})

    if any(term in lower for term in ("self-driving", "autonomous", "drone")):
        add("Everyday use versus demonstration",
            "How widely are the autonomous services shown in this clip used, and what distinguishes everyday use from limited demonstrations?",
            "Identify the vehicles or delivery services described in the clip. Check their operating areas, level of autonomy, restrictions, and evidence of regular use. Compare the creator's observations with operator documentation and independent reporting.")
    if any(term in lower for term in ("huaqiangbei", "electronics market", "tech stores", "component")):
        add("What physical markets offer",
            "What does the electronics market described in this clip offer that online marketplaces cannot?",
            "Investigate component availability, repairs, customization, and prototyping. Test the clip's comparison with online shopping using vendor examples and buyer experiences, distinguishing ordinary retail from specialist services.")
    if any(term in lower for term in ("smart glasses", "smartwatch", "wearable")):
        add("Features versus practical value",
            "Do the wearable features described in this clip provide meaningful everyday benefits compared with existing devices?",
            "List the features actually described, then seek independent tests of usefulness, comfort, battery life, and privacy. Compare the featured device with relevant alternatives and separate demonstrated results from promotional claims.")
    if any(term in lower for term in ("sponsor", "sponsored", "paid partnership")):
        add("Independent assessment of sponsorship claims",
            "Which claims in this sponsored segment are independently supported, and which still need testing?",
            "Identify the sponsorship disclosure and each checkable product claim. Compare manufacturer documentation with independent reviews, note missing limitations, and avoid treating sponsorship alone as proof that a claim is false.")
    if any(term in lower for term in ("visiting", "trip", "travels")) and any(
        term in lower for term in ("city", "nature", "architecture", "public spaces")
    ):
        add("Visitor impressions and daily life",
            "How representative are the visitor's impressions in this clip of residents' everyday experience?",
            "Identify the places and benefits the speaker describes. Compare that experience with evidence about accessibility, affordability, transport, and public spaces. Distinguish personal travel impressions from broader claims about life in the destination.")

    # Prefer a complete, substantive sentence over an opening transcript fragment.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    anchor = next((s for s in sentences if 45 <= len(s) <= 240), text[:237])
    if len(anchor) < len(text) and anchor == text[:237]:
        anchor = anchor.rsplit(" ", 1)[0] + "…"
    add("Check a central statement",
        f"What evidence supports or challenges this statement from the clip: “{anchor}”?",
        f"Start with the speaker's statement: “{anchor}”. Identify its checkable claims, find primary sources and independent corroboration, and record uncertainty. Distinguish observations and opinions from conclusions that require external evidence.")
    add("Find missing context",
        f"What context would help viewers assess the clip's discussion of “{anchor}”?",
        "Examine the assumptions, comparisons, and scope behind this passage. Look for relevant counterexamples and perspectives absent from the clip. Explain how additional context changes the interpretation without assuming the speaker is wrong.")
    add("Explore implications",
        f"What practical implications follow from the clip's statement “{anchor}”, if it is supported?",
        "Establish what the passage actually demonstrates before discussing its implications. Identify who could benefit or face tradeoffs, check evidence for those effects, and clearly label any interpretation or prediction.")
    return suggestions[:3]


def suggestions_for_clip(clip, user):
    ensure_editorial_allowed(clip.project, user)
    if clip.status != SourceClip.Status.APPROVED:
        raise EditorialServiceError("Approve this clip before requesting research suggestions.")
    if SourceClipService.is_stale(clip):
        raise EditorialServiceError("The source clip inputs changed; regenerate and approve the clip first.")
    return suggest_from_transcript(clip.selected_segment.analysis_segment.source_text)
