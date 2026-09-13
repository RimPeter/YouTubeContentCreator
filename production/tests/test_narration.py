import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from editorial.services.reactions import ReactionService
from production.models import MediaAsset, NarrationTake
from production.services.access import ProductionValidationError
from production.services.narration import NarrationProbe, NarrationService
from production.services.probe import MediaMetadata, MediaProbeError
from editorial.tests.helpers import EditorialTestCase, create_ready_package
from .helpers import FakeProbeClient


def audio_metadata(*, duration=Decimal("42.000"), video=False):
    return MediaMetadata(duration_seconds=duration, width=1920 if video else None,
                         height=1080 if video else None, frame_rate=Decimal("30") if video else None,
                         has_audio=True, has_video=video, detected_mime_type="audio/mpeg",
                         container="mp3", video_codec="h264" if video else "", audio_codec="mp3")


class NarrationTests(EditorialTestCase):
    def setUp(self):
        self.user, self.project, _, _, self.clip, self.package = create_ready_package("narration-owner")
        self.reaction = ReactionService().generate(self.package, self.user)
        ReactionService.approve(self.reaction, self.user, evidence_reviewed=True, originality_confirmed=True)
        self.temporary_media = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.temporary_media.name)
        self.override.enable()
        self.client.force_login(self.user)

    def tearDown(self):
        self.override.disable()
        self.temporary_media.cleanup()

    def upload(self, name="recording.mp3"):
        return SimpleUploadedFile(name, b"narration", content_type="audio/mpeg")

    def test_audio_upload_is_linked_to_exact_approved_script_and_playable(self):
        take = NarrationService.upload(
            self.reaction, self.upload(), self.user, rights_basis="user_owned", rights_confirmed=True,
            probe_client=FakeProbeClient(metadata=audio_metadata()),
        )
        self.assertEqual(take.version, 1)
        self.assertEqual(take.script_text, self.reaction.combined_script)
        self.assertEqual(take.script_fingerprint, NarrationService.fingerprint(self.reaction))
        self.assertEqual(take.media_asset.kind, MediaAsset.Kind.NARRATION)
        self.assertTrue(take.media_asset.has_audio)
        self.assertFalse(take.media_asset.has_video)
        self.assertEqual(take.media_asset.duration_seconds, Decimal("42.000"))
        response = self.client.get(reverse("production:stream_narration", args=[take.pk]), HTTP_RANGE="bytes=2-5")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b"".join(response.streaming_content), b"rrat")
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_rejects_video_bad_duration_changed_script_and_other_user(self):
        for metadata in (audio_metadata(video=True), audio_metadata(duration=Decimal("0")),
                         audio_metadata(duration=Decimal("3601"))):
            with self.subTest(metadata=metadata):
                with self.assertRaises(ProductionValidationError):
                    NarrationService.upload(self.reaction, self.upload(), self.user, rights_basis="user_owned",
                                            rights_confirmed=True, probe_client=FakeProbeClient(metadata=metadata))
        type(self.package).objects.filter(pk=self.package.pk).update(status="draft")
        with self.assertRaisesMessage(Exception, "current approved reaction"):
            NarrationService.upload(self.reaction, self.upload(), self.user, rights_basis="user_owned",
                                    rights_confirmed=True, probe_client=FakeProbeClient(metadata=audio_metadata()))
        self.assertEqual(NarrationTake.objects.count(), 0)

    def test_view_requires_post_rights_and_project_access(self):
        url = reverse("editorial:upload_narration", args=[self.reaction.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {"media_file": self.upload(), "rights_basis": "user_owned"})
        self.assertRedirects(response, reverse("editorial:reaction_detail", args=[self.reaction.pk]))
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user("narration-other")
        self.client.force_login(other)
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_probe_rejects_invalid_audio(self):
        with self.assertRaises(MediaProbeError):
            NarrationProbe(FakeProbeClient(metadata=audio_metadata(video=True))).probe("unused.mp3", ".mp3")
