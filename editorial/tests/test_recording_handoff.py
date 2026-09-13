from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from editorial.tests.helpers import EditorialTestCase, create_approved_clip
from editorial.models import ReactionProductionAssembly
from editorial.services.reaction_sequence_plans import ReactionSequencePlanService
from editorial.services.reaction_sequence_drafts import ReactionSequenceDraftService
from editorial.services.reaction_timelines import ReactionTimelineService
from editorial.services.reaction_production import ReactionProductionService as Service
from production.tests.test_narration import audio_metadata


class RecordingHandoffTests(EditorialTestCase):
    def setUp(self):
        self.user, self.project, self.source, self.selection, self.clip = create_approved_clip("recording-owner")
        plan = ReactionSequencePlanService.create(self.project, self.user)
        ReactionSequencePlanService.mark_ready(plan, self.user, research_reviewed=True)
        draft = ReactionSequenceDraftService.generate(plan, self.user, use_ai=False)
        self.timeline = ReactionTimelineService.create(draft, self.user)
        self.client.force_login(self.user)

    def post(self, route, pk, data=None):
        response = self.client.post(reverse("editorial:" + route, args=[pk]), data or {})
        self.assertEqual(response.status_code, 302)
        return response

    def record_all(self):
        self.post("review_recording_script", self.timeline.pk)
        for item in self.timeline.items.filter(item_type="creator"):
            with patch("production.services.narration.FFprobeClient.probe", return_value=audio_metadata()):
                self.post("upload_timeline_narration", item.pk, {
                    "media_file": SimpleUploadedFile("take.mp3", b"recorded narration"),
                    "rights_basis": "user_owned", "rights_confirmed": "on", "recording_notes": "Take notes",
                })
            take = item.narration_takes.latest("version")
            self.post("approve_timeline_narration", take.pk)

    def test_owner_forms_recording_playback_and_assembly(self):
        self.post("add_timeline_creator_item", self.timeline.pk, {"label": "Add-on", "transcript_text": "One more thought."})
        item = self.timeline.items.last()
        self.post("edit_timeline_creator_item", item.pk, {"label": "Revised", "transcript_text": "My own words.", "included": "on"})
        self.post("move_timeline_item", item.pk, {"direction": "up"})
        source = self.timeline.items.get(item_type="source")
        self.post("edit_timeline_source_item", source.pk, {"source_start_seconds": 1.1, "source_end_seconds": 2.9, "included": "on"})
        self.record_all()
        response = self.client.get(reverse("editorial:recording_review", args=[self.timeline.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["issues"], [])
        self.assertEqual(response.context["timeline"].status, "ready")
        take = self.timeline.items.filter(item_type="creator").first().narration_takes.first()
        audio = self.client.get(reverse("editorial:timeline_take_media", args=[take.pk]))
        self.assertEqual(audio.status_code, 200)
        self.assertTrue(b"".join(audio.streaming_content))
        self.post("create_reaction_assembly", self.timeline.pk)
        assembly = self.timeline.production_assemblies.get()
        self.assertFalse(Service.is_assembly_stale(assembly))
        self.assertEqual(assembly.items.count(), self.timeline.items.filter(included=True).count())
        snapshot = assembly.items.first().snapshot.copy()
        self.client.get(reverse("editorial:recording_review", args=[self.timeline.pk]))
        assembly.refresh_from_db()
        self.assertFalse(Service.is_assembly_stale(assembly))
        self.assertEqual(self.client.get(reverse("editorial:reaction_assembly_detail", args=[assembly.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("editorial:recording_script_download", args=[self.timeline.pk])), "My own words.")
        self.post("create_reaction_assembly", self.timeline.pk)
        self.assertEqual(self.timeline.production_assemblies.count(), 2)
        creator = self.timeline.items.filter(item_type="creator").first()
        self.post("edit_timeline_creator_item", creator.pk, {"label": "Changed", "transcript_text": "Changed after recording", "included": "on"})
        self.assertTrue(Service.is_assembly_stale(assembly))
        self.assertEqual(assembly.items.first().snapshot, snapshot)
        self.post("create_reaction_assembly", self.timeline.pk)
        self.assertEqual(self.timeline.production_assemblies.count(), 2)

    def test_replacement_selection_and_missing_media(self):
        self.record_all()
        item = self.timeline.items.filter(item_type="creator").first()
        first = item.narration_takes.first()
        with patch("production.services.narration.FFprobeClient.probe", return_value=audio_metadata()):
            self.post("upload_timeline_narration", item.pk, {"media_file": SimpleUploadedFile("second.mp3", b"second"),
                "rights_basis": "user_owned", "rights_confirmed": "on"})
        second = item.narration_takes.latest("version")
        self.assertTrue(item.narration_takes.get(pk=first.pk).selected)
        self.post("approve_timeline_narration", second.pk)
        self.assertEqual(item.narration_takes.filter(selected=True).get().pk, second.pk)
        self.post("approve_timeline_narration", first.pk)
        self.assertEqual(item.narration_takes.filter(selected=True).get().pk, first.pk)
        self.clip.processed_asset.file.delete(save=False)
        self.timeline.refresh_from_db()
        self.assertTrue(any("source clip" in issue for issue in Service.readiness(self.timeline)[0]))

    def test_ownership_and_upload_rejection(self):
        item = self.timeline.items.filter(item_type="creator").first()
        self.post("upload_timeline_narration", item.pk, {"media_file": SimpleUploadedFile("bad.mp3", b"bad"),
            "rights_basis": "user_owned", "rights_confirmed": "on"})
        self.assertFalse(item.narration_takes.exists())
        self.record_all()
        take = item.narration_takes.first()
        self.client.force_login(get_user_model().objects.create_user("outsider"))
        for route, pk in [("edit_timeline_creator_item", item.pk), ("move_timeline_item", item.pk),
                          ("upload_timeline_narration", item.pk), ("approve_timeline_narration", take.pk),
                          ("create_reaction_assembly", self.timeline.pk)]:
            self.assertEqual(self.client.post(reverse("editorial:" + route, args=[pk])).status_code, 404)
        for route, pk in [("timeline_take_media", take.pk), ("recording_review", self.timeline.pk),
                          ("recording_script_download", self.timeline.pk)]:
            self.assertEqual(self.client.get(reverse("editorial:" + route, args=[pk])).status_code, 404)

    def test_upstream_changes_invalidate_without_visiting_timeline(self):
        self.record_all()
        self.post("create_reaction_assembly", self.timeline.pk)
        assembly = ReactionProductionAssembly.objects.get()
        self.selection.notes = "Changed context"
        self.selection.save()
        self.assertTrue(Service.is_assembly_stale(assembly))

    def test_trim_exclusion_and_snapshot_download(self):
        self.record_all()
        self.post("create_reaction_assembly", self.timeline.pk)
        assembly = self.timeline.production_assemblies.get()
        url = reverse("editorial:assembly_script_download", args=[assembly.pk])
        saved = self.client.get(url).content
        source = self.timeline.items.get(item_type="source")
        self.post("edit_timeline_source_item", source.pk, {"source_start_seconds": 1, "source_end_seconds": 1, "included": "on"})
        source.refresh_from_db()
        self.assertEqual(source.source_end_seconds, 3)
        self.post("edit_timeline_source_item", source.pk, {"source_start_seconds": 1.2, "source_end_seconds": 2.8, "included": "on"})
        self.assertTrue(Service.is_assembly_stale(assembly))
        self.assertEqual(self.client.get(url).content, saved)
        self.post("review_recording_script", self.timeline.pk)
        self.timeline.refresh_from_db()
        self.assertEqual(Service.readiness(self.timeline)[0], [])
        self.timeline.items.filter(item_type="creator").update(included=False)
        self.assertTrue(any("at least one creator" in message for message in Service.script_issues(self.timeline)))

    def test_failed_upload_and_changed_script(self):
        from production.models import MediaAsset
        self.post("review_recording_script", self.timeline.pk)
        item = self.timeline.items.filter(item_type="creator").first()
        with patch("production.services.narration.FFprobeClient.probe", return_value=audio_metadata(video=True)):
            self.post("upload_timeline_narration", item.pk, {"media_file": SimpleUploadedFile("bad.mp3", b"video"),
                "rights_basis": "user_owned", "rights_confirmed": "on"})
        self.assertFalse(item.narration_takes.exists())
        def changed(*args):
            item.transcript_text = "Changed during upload"
            item.save(update_fields=["transcript_text"])
            return audio_metadata()
        with patch("production.services.narration.FFprobeClient.probe", side_effect=changed):
            self.post("upload_timeline_narration", item.pk, {"media_file": SimpleUploadedFile("race.mp3", b"audio"),
                "rights_basis": "user_owned", "rights_confirmed": "on"})
        self.assertFalse(item.narration_takes.exists())
        self.assertTrue(MediaAsset.objects.filter(kind="narration", status="failed").exists())
