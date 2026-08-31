from typing import Protocol


class AnalysisProvider(Protocol):
    name: str
    model: str
    prompt_version: str

    def analyze(self, transcript, configuration):
        """Return a structured mapping containing a `segments` list."""
