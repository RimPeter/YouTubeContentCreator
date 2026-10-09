from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math
from .summaries import fallback_excerpt


SCORE_DIMENSIONS = (
    "relevance",
    "clarity",
    "factual_density",
    "novelty",
    "controversy",
    "reaction_potential",
    "clip_suitability",
)

EDITORIAL_APPROACHES = (
    "review",
    "critic",
    "reflect",
    "add_on",
    "explain",
    "verify",
    "compare",
    "question",
    "pass",
)

DEFAULT_SCORE_WEIGHTS = {
    "relevance": 0.20,
    "clarity": 0.15,
    "factual_density": 0.15,
    "novelty": 0.10,
    "controversy": 0.10,
    "reaction_potential": 0.20,
    "clip_suitability": 0.10,
}

DEFAULT_CONFIGURATION = {
    "score_weights": DEFAULT_SCORE_WEIGHTS,
    "fallback_max_chunks": 20,
    "max_provider_attempts": 2,
}


class AnalysisOutputValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSegment:
    order: int
    start_chunk: object
    end_chunk: object
    start_seconds: float
    end_seconds: float
    title: str
    summary: str
    source_text: str
    topic_labels: list
    component_scores: dict
    aggregate_score: Decimal
    rationale: str
    editorial_recommendation: dict


def normalize_configuration(configuration=None):
    supplied = dict(configuration or {})
    unknown = set(supplied) - set(DEFAULT_CONFIGURATION)
    if unknown:
        raise AnalysisOutputValidationError(
            f"Unknown analysis configuration keys: {', '.join(sorted(unknown))}."
        )
    weights = supplied.get("score_weights", DEFAULT_SCORE_WEIGHTS)
    if not isinstance(weights, dict) or set(weights) != set(SCORE_DIMENSIONS):
        raise AnalysisOutputValidationError("Score weights must define every supported dimension.")
    normalized_weights = {}
    for name in SCORE_DIMENSIONS:
        value = weights[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AnalysisOutputValidationError(f"Weight {name} must be numeric.")
        if not math.isfinite(value) or value < 0:
            raise AnalysisOutputValidationError(f"Weight {name} must be finite and non-negative.")
        normalized_weights[name] = float(value)
    if not math.isclose(sum(normalized_weights.values()), 1.0, abs_tol=0.000001):
        raise AnalysisOutputValidationError("Score weights must sum to 1.0.")

    fallback_max_chunks = supplied.get("fallback_max_chunks", 20)
    max_provider_attempts = supplied.get("max_provider_attempts", 2)
    if isinstance(fallback_max_chunks, bool) or not isinstance(fallback_max_chunks, int):
        raise AnalysisOutputValidationError("fallback_max_chunks must be an integer.")
    if not 1 <= fallback_max_chunks <= 100:
        raise AnalysisOutputValidationError("fallback_max_chunks must be between 1 and 100.")
    if isinstance(max_provider_attempts, bool) or not isinstance(max_provider_attempts, int):
        raise AnalysisOutputValidationError("max_provider_attempts must be an integer.")
    if not 1 <= max_provider_attempts <= 3:
        raise AnalysisOutputValidationError("max_provider_attempts must be between 1 and 3.")
    return {
        "score_weights": normalized_weights,
        "fallback_max_chunks": fallback_max_chunks,
        "max_provider_attempts": max_provider_attempts,
    }


def _required_text(item, key, max_length):
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AnalysisOutputValidationError(f"Segment {key} must be non-empty text.")
    value = value.strip()
    if len(value) > max_length:
        raise AnalysisOutputValidationError(f"Segment {key} exceeds {max_length} characters.")
    return value


def _validate_scores(scores, weights):
    if not isinstance(scores, dict) or set(scores) != set(SCORE_DIMENSIONS):
        raise AnalysisOutputValidationError("Every segment must provide exactly the supported scores.")
    normalized = {}
    aggregate = Decimal("0")
    for name in SCORE_DIMENSIONS:
        value = scores[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AnalysisOutputValidationError(f"Score {name} must be numeric.")
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise AnalysisOutputValidationError(f"Score {name} must be between 0 and 100.")
        normalized[name] = float(value)
        aggregate += Decimal(str(value)) * Decimal(str(weights[name]))
    return normalized, aggregate.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _validate_editorial_recommendation(recommendation):
    required = {
        "primary_approach",
        "secondary_approaches",
        "confidence",
        "reasoning",
        "suggested_angle",
        "research_needed",
    }
    if not isinstance(recommendation, dict) or set(recommendation) != required:
        raise AnalysisOutputValidationError(
            "Every segment must provide a complete editorial recommendation."
        )
    primary = recommendation["primary_approach"]
    secondary = recommendation["secondary_approaches"]
    confidence = recommendation["confidence"]
    if primary not in EDITORIAL_APPROACHES:
        raise AnalysisOutputValidationError("Editorial recommendation uses an unsupported primary approach.")
    if (
        not isinstance(secondary, list)
        or len(secondary) > 2
        or len(secondary) != len(set(secondary))
        or primary in secondary
        or any(item not in EDITORIAL_APPROACHES for item in secondary)
    ):
        raise AnalysisOutputValidationError("Secondary editorial approaches are invalid.")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise AnalysisOutputValidationError("Editorial recommendation confidence must be numeric.")
    if not math.isfinite(confidence) or not 0 <= confidence <= 100:
        raise AnalysisOutputValidationError("Editorial recommendation confidence must be between 0 and 100.")
    if not isinstance(recommendation["research_needed"], bool):
        raise AnalysisOutputValidationError("Editorial recommendation research_needed must be boolean.")
    return {
        "primary_approach": primary,
        "secondary_approaches": secondary,
        "confidence": float(confidence),
        "reasoning": _required_text(recommendation, "reasoning", 2000),
        "suggested_angle": _required_text(recommendation, "suggested_angle", 2000),
        "research_needed": recommendation["research_needed"],
    }


def validate_provider_output(output, chunks, weights):
    nonempty_chunks = [chunk for chunk in chunks if chunk.text.strip()]
    if not nonempty_chunks:
        raise AnalysisOutputValidationError("The transcript contains no non-empty chunks.")
    if not isinstance(output, dict) or not isinstance(output.get("segments"), list):
        raise AnalysisOutputValidationError("Provider output must contain a segments list.")
    if not output["segments"]:
        raise AnalysisOutputValidationError("Provider output contains no segments.")

    sequence_to_index = {chunk.sequence: index for index, chunk in enumerate(nonempty_chunks)}
    expected_start_index = 0
    validated = []
    for order, item in enumerate(output["segments"], start=1):
        if not isinstance(item, dict):
            raise AnalysisOutputValidationError("Every segment must be an object.")
        start_sequence = item.get("start_sequence")
        end_sequence = item.get("end_sequence")
        if (
            isinstance(start_sequence, bool)
            or isinstance(end_sequence, bool)
            or not isinstance(start_sequence, int)
            or not isinstance(end_sequence, int)
        ):
            raise AnalysisOutputValidationError("Segment boundaries must be integer chunk sequences.")
        if start_sequence not in sequence_to_index or end_sequence not in sequence_to_index:
            raise AnalysisOutputValidationError("Segment boundary does not reference a non-empty chunk.")
        start_index = sequence_to_index[start_sequence]
        end_index = sequence_to_index[end_sequence]
        if start_index != expected_start_index:
            raise AnalysisOutputValidationError("Segments contain a gap, overlap, or are out of order.")
        if end_index < start_index:
            raise AnalysisOutputValidationError("Segment end precedes its start.")

        segment_chunks = nonempty_chunks[start_index:end_index + 1]
        labels = item.get("topic_labels", [])
        if (
            not isinstance(labels, list)
            or len(labels) > 20
            or any(not isinstance(label, str) or not label.strip() or len(label) > 100 for label in labels)
        ):
            raise AnalysisOutputValidationError("topic_labels must contain up to 20 short strings.")
        normalized_scores, aggregate = _validate_scores(item.get("scores"), weights)
        editorial_recommendation = _validate_editorial_recommendation(
            item.get("editorial_recommendation")
        )
        start_chunk = segment_chunks[0]
        end_chunk = segment_chunks[-1]
        validated.append(
            ValidatedSegment(
                order=order,
                start_chunk=start_chunk,
                end_chunk=end_chunk,
                start_seconds=start_chunk.start_seconds,
                end_seconds=end_chunk.start_seconds + end_chunk.duration_seconds,
                title=_required_text(item, "title", 255),
                summary=_required_text(item, "summary", 4000),
                source_text=" ".join(chunk.text.strip() for chunk in segment_chunks),
                topic_labels=[label.strip() for label in labels],
                component_scores=normalized_scores,
                aggregate_score=aggregate,
                rationale=_required_text(item, "rationale", 4000),
                editorial_recommendation=editorial_recommendation,
            )
        )
        expected_start_index = end_index + 1
    if expected_start_index != len(nonempty_chunks):
        raise AnalysisOutputValidationError("Segments do not cover the complete non-empty transcript.")
    return validated


def build_fallback_output(chunks, max_chunks):
    nonempty_chunks = [chunk for chunk in chunks if chunk.text.strip()]
    if not nonempty_chunks:
        raise AnalysisOutputValidationError("The transcript contains no non-empty chunks.")
    segments = []
    for offset in range(0, len(nonempty_chunks), max_chunks):
        group = nonempty_chunks[offset:offset + max_chunks]
        text = " ".join(chunk.text.strip() for chunk in group)
        number = len(segments) + 1
        segments.append(
            {
                "start_sequence": group[0].sequence,
                "end_sequence": group[-1].sequence,
                "title": f"Transcript section {number}",
                "summary": fallback_excerpt(text),
                "topic_labels": ["deterministic-fallback"],
                "scores": {name: 50.0 for name in SCORE_DIMENSIONS},
                "rationale": "Deterministic fallback segment; human editorial review is required.",
                "editorial_recommendation": {
                    "primary_approach": "add_on",
                    "secondary_approaches": [],
                    "confidence": 0,
                    "reasoning": "This fallback section was grouped by transcript position, not topic analysis.",
                    "suggested_angle": "Review the section and add context before using it in a clip.",
                    "research_needed": False,
                },
            }
        )
    return {"segments": segments}
