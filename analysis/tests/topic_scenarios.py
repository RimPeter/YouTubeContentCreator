"""Editorial boundary scenarios for topic-segmentation regression tests.

The expected boundaries describe the intended editorial grouping, not exact model wording.
"""

from .helpers import editorial_recommendation, scores


def _output(boundaries):
    return {
        "segments": [
            {
                "start_sequence": start,
                "end_sequence": end,
                "title": title,
                "summary": f"A coherent section about {title.lower()}.",
                "topic_labels": [title.lower()],
                "scores": scores(70),
                "rationale": "The remarks form one self-contained editorial topic.",
                "editorial_recommendation": editorial_recommendation("add_on"),
            }
            for start, end, title in boundaries
        ]
    }


TOPIC_SCENARIOS = (
    {
        "name": "interview",
        "chunks": [
            "Host: What first made you build the product?",
            "Guest: I needed it while managing my own team.",
            "Host: How did you find the first customers?",
            "Guest: We interviewed small business owners every week.",
        ],
        "boundaries": [(1, 2, "Origin story"), (3, 4, "Early customer research")],
    },
    {
        "name": "monologue",
        "chunks": [
            "The hook fails when it promises a result the video never gives.",
            "A stronger hook names the problem and the reason to continue.",
            "Now let us look at the edit that keeps viewers after the hook.",
            "Cut pauses that do not add tension or explanation.",
        ],
        "boundaries": [(1, 2, "Writing an honest hook"), (3, 4, "Editing for retention")],
    },
    {
        "name": "podcast",
        "chunks": [
            "Speaker one: Remote work changed our hiring geography.",
            "Speaker two: It also changed how new hires learn informally.",
            "Speaker one: The compensation picture is more complicated.",
            "Speaker two: Local salary bands still affect many offers.",
        ],
        "boundaries": [(1, 2, "Remote hiring"), (3, 4, "Compensation tradeoffs")],
    },
    {
        "name": "abrupt_topic_change",
        "chunks": [
            "The launch failed because the onboarding email arrived too late.",
            "We fixed activation by sending the walkthrough immediately.",
            "On a different note, I trained for my first marathon this spring.",
            "Long runs taught me to pace the first kilometre slowly.",
        ],
        "boundaries": [(1, 2, "Product onboarding"), (3, 4, "Marathon training")],
    },
    {
        "name": "repeated_topic",
        "chunks": [
            "A good thumbnail creates a specific question in the viewer's mind.",
            "Avoid hiding the subject behind tiny text.",
            "The interview then moved to pricing experiments.",
            "After the pricing discussion, they returned to thumbnails with a new example.",
            "That example used one visible object instead of three competing ideas.",
        ],
        "boundaries": [(1, 2, "Thumbnail principles"), (3, 3, "Pricing experiments"), (4, 5, "Thumbnail example")],
    },
    {
        "name": "sparse_transcript",
        "chunks": ["Yes.", "The key result was a 20 percent reduction in churn.", "Exactly."],
        "boundaries": [(1, 3, "Churn result")],
    },
    {
        "name": "long_transcript",
        "chunks": [
            f"Part {part}: detailed remarks on topic {1 if part <= 10 else 2 if part <= 20 else 3}."
            for part in range(1, 31)
        ],
        "boundaries": [(1, 10, "Topic one"), (11, 20, "Topic two"), (21, 30, "Topic three")],
    },
)
