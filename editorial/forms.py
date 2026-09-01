from django import forms

from scraper.models import TranscriptChunk

from .models import EvidenceSource, ReactionBlock, ReactionClaim, ResearchPackage


class ResearchPackageForm(forms.ModelForm):
    class Meta:
        model = ResearchPackage
        fields = ["research_question", "editorial_focus"]


class EvidenceSourceForm(forms.ModelForm):
    class Meta:
        model = EvidenceSource
        fields = [
            "source_url", "title", "publisher", "author", "publication_date",
            "retrieved_on", "classification", "finding", "relevance",
            "permitted_excerpt", "snapshot_reference",
        ]
        widgets = {
            "publication_date": forms.DateInput(attrs={"type": "date"}),
            "retrieved_on": forms.DateInput(attrs={"type": "date"}),
        }


class ReactionBlockForm(forms.Form):
    reframe = forms.CharField(max_length=4000, widget=forms.Textarea)
    focus = forms.CharField(max_length=4000, widget=forms.Textarea)
    reaction_type = forms.ChoiceField(choices=ReactionBlock.ReactionType.choices)
    evaluation = forms.CharField(max_length=8000, widget=forms.Textarea)
    mini_essay_thesis = forms.CharField(max_length=2000, widget=forms.Textarea)
    mini_essay_script = forms.CharField(max_length=16000, widget=forms.Textarea)
    conclusion = forms.CharField(max_length=4000, widget=forms.Textarea)
    bridge = forms.CharField(max_length=2000, widget=forms.Textarea, required=False)
    rationale = forms.CharField(max_length=4000, widget=forms.Textarea)

    def __init__(self, *args, block=None, **kwargs):
        super().__init__(*args, **kwargs)
        if block and not self.is_bound:
            for name in self.fields:
                self.initial[name] = getattr(block, name)


class ReactionClaimForm(forms.ModelForm):
    class Meta:
        model = ReactionClaim
        fields = ["text", "claim_type", "transcript_chunk", "evidence_source", "citation_note"]

    def __init__(self, *args, block, **kwargs):
        super().__init__(*args, **kwargs)
        segment = block.source_clip.selected_segment.analysis_segment
        self.fields["transcript_chunk"].queryset = TranscriptChunk.objects.filter(
            source_video=segment.analysis_run.source_video,
            sequence__gte=segment.start_chunk.sequence,
            sequence__lte=segment.end_chunk.sequence,
        )
        self.fields["evidence_source"].queryset = block.research_package.evidence_sources.filter(
            verification_status=EvidenceSource.VerificationStatus.VERIFIED
        )
