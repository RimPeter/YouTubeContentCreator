from pathlib import Path

from django.contrib.auth import get_user_model
from django.db.models import Max
from django.utils import timezone

from editorial.services.access import EditorialServiceError, editorial_transaction
from editorial.services.fingerprints import reaction_fingerprint
from editorial.services.reactions import ReactionService
from editorial.models import ReactionBlock
from production.models import MediaAsset, NarrationTake
from .fingerprints import fingerprint_json
from .media_assets import MediaAssetService
from .probe import FFprobeClient, MediaProbeError


class NarrationProbe:
    def __init__(self, client=None):
        self.client = client or FFprobeClient()

    def probe(self, path, extension):
        metadata = self.client.probe(path, extension)
        if (not metadata.has_audio or metadata.has_video or metadata.duration_seconds is None
                or not metadata.duration_seconds.is_finite() or not 0 < metadata.duration_seconds <= 3600):
            raise MediaProbeError("invalid_narration", "Upload audio-only narration with a duration of 1 hour or less.")
        return metadata


class NarrationService:
    @staticmethod
    def fingerprint(reaction):
        return fingerprint_json({"reaction_id": reaction.pk, "version": reaction.version,
                                 "script": reaction.combined_script})

    @staticmethod
    def current(reaction, user):
        if reaction.status != ReactionBlock.Status.APPROVED:
            return False
        package = ReactionService._package(reaction.research_package_id)
        try:
            ReactionService._validate_inputs(package, user)
        except EditorialServiceError:
            return False
        return reaction.input_fingerprint == reaction_fingerprint(package)

    @classmethod
    def upload(cls, reaction, uploaded_file, user, *, rights_basis, rights_notes="", rights_confirmed=False,
               probe_client=None):
        if not rights_confirmed or rights_basis not in {"user_owned", "licensed", "permission", "public_domain"}:
            raise EditorialServiceError("Confirm your authorization to use this recording.")
        if Path(uploaded_file.name).suffix.lower() not in {".wav", ".mp3", ".m4a", ".aac"}:
            raise EditorialServiceError("Upload a WAV, MP3, M4A or AAC recording.")
        with editorial_transaction(reaction.project_id, user):
            reaction = ReactionBlock.objects.get(pk=reaction.pk)
            if not cls.current(reaction, user):
                raise EditorialServiceError("Narration requires a current approved reaction script.")
            script = reaction.combined_script
            fingerprint = cls.fingerprint(reaction)
        asset = MediaAssetService(NarrationProbe(probe_client)).create_upload(
            reaction.project, uploaded_file, user, kind=MediaAsset.Kind.NARRATION,
            rights_basis=rights_basis, rights_notes=rights_notes,
            consent_metadata={"rights_confirmed": True, "confirmed_by": user.pk},
        )
        try:
            current_user = get_user_model().objects.get(pk=user.pk)
            with editorial_transaction(reaction.project_id, current_user):
                reaction.refresh_from_db()
                if not cls.current(reaction, current_user) or cls.fingerprint(reaction) != fingerprint:
                    raise EditorialServiceError("The script changed during upload. Upload narration for the current script.")
                version = (reaction.narration_takes.aggregate(latest=Max("version"))["latest"] or 0) + 1
                take = NarrationTake(project_id=reaction.project_id, reaction=reaction, media_asset=asset,
                                     version=version, script_text=script, script_fingerprint=fingerprint)
                take.full_clean()
                take.save()
                return take
        except Exception:
            MediaAsset.objects.filter(pk=asset.pk).update(
                status=MediaAsset.Status.FAILED, error_code="narration_inputs_changed",
                error_message="Narration could not be attached to a current script.", updated_at=timezone.now())
            raise
