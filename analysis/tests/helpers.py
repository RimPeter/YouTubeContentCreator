from django.contrib.auth import get_user_model
from django.utils import timezone

from scraper.models import SourceVideo, TranscriptChunk, VideoProject


SCORE_NAMES = (
    "relevance",
    "clarity",
    "factual_density",
    "novelty",
    "controversy",
    "reaction_potential",
    "clip_suitability",
)


def create_approved_source(username="analysis-owner", chunk_count=4):
    user = get_user_model().objects.create_user(username=username, password="password")
    project = VideoProject.objects.create(
        owner=user,
        title=f"{username} project",
        status=VideoProject.Status.APPROVED,
        approved_at=timezone.now(),
    )
    source = SourceVideo.objects.create(
        project=project,
        youtube_url="https://youtu.be/dQw4w9WgXcQ",
        youtube_video_id="dQw4w9WgXcQ",
        title="Analysis source",
        transcript_status=SourceVideo.TranscriptStatus.COMPLETED,
    )
    chunks = [
        TranscriptChunk.objects.create(
            source_video=source,
            sequence=index,
            start_seconds=float(index - 1) * 2,
            duration_seconds=2.0,
            text=f"Chunk {index} text",
        )
        for index in range(1, chunk_count + 1)
    ]
    return user, project, source, chunks


def scores(value=80):
    return {name: value for name in SCORE_NAMES}


def editorial_recommendation(primary="critic", secondary=None, research_needed=False):
    return {
        "primary_approach": primary,
        "secondary_approaches": secondary or [],
        "confidence": 80,
        "reasoning": "The segment has a clear claim worth addressing editorially.",
        "suggested_angle": "Add a concrete audience-facing perspective to this claim.",
        "research_needed": research_needed,
    }


def valid_provider_output():
    return {
        "segments": [
            {
                "start_sequence": 1,
                "end_sequence": 2,
                "title": "First topic",
                "summary": "The first two chunks.",
                "topic_labels": ["first"],
                "scores": scores(80),
                "rationale": "Strong opening reaction material.",
                "editorial_recommendation": editorial_recommendation("reflect"),
            },
            {
                "start_sequence": 3,
                "end_sequence": 4,
                "title": "Second topic",
                "summary": "The final two chunks.",
                "topic_labels": ["second"],
                "scores": scores(60),
                "rationale": "Useful follow-up reaction material.",
                "editorial_recommendation": editorial_recommendation("verify", research_needed=True),
            },
        ]
    }


class FakeProvider:
    name = "fake-provider"
    model = "fake-model"
    prompt_version = "prompt-v1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def analyze(self, transcript, configuration):
        self.calls += 1
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response
