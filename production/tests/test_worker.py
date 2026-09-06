import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from production.models import ArtifactDependency, MediaAsset, PipelineJob, SourceClip
from production.services.clips import SourceClipError, SourceClipService
from production.services.jobs import PipelineJobService, PipelineJobStateError
from production.services.media_assets import MediaAssetService
from production.services.probe import MediaMetadata

from .helpers import FakeProbeClient, create_selected_segment, create_source_upload
from .test_clips import FakeFFmpegClient


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    ALLOWED_HOSTS=["testserver"],
    PIPELINE_RETRY_DELAY_SECONDS=0,
    PIPELINE_LEASE_SECONDS=30,
)
class PipelineWorkerTests(TestCase):
    def setUp(self):
        self.user, self.project, self.source, self.selection = create_selected_segment("worker-owner")
        self.asset = create_source_upload(self.project, self.user)
        self.ffmpeg = FakeFFmpegClient()
        metadata = MediaMetadata(
            duration_seconds=Decimal("2.000"), width=320, height=180,
            frame_rate=Decimal("30"), has_audio=True, has_video=True,
            detected_mime_type="video/mp4", container="mov,mp4",
            video_codec="h264", audio_codec="aac",
        )
        self.service = SourceClipService(
            ffmpeg_client=self.ffmpeg,
            media_service=MediaAssetService(FakeProbeClient(metadata=metadata)),
        )
        self.clip, _ = self.service.enqueue_clip(self.selection, self.asset, self.user)
        self.job = self.clip.pipeline_job

    def expire(self, attempt):
        PipelineJob.objects.filter(pk=attempt.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))

    def test_enqueue_is_durable_without_running_ffmpeg_and_deduplicates_active_work(self):
        self.assertEqual(self.ffmpeg.calls, [])
        self.assertEqual(self.job.status, PipelineJob.Status.QUEUED)
        self.assertEqual(self.clip.status, SourceClip.Status.PROCESSING)
        self.assertIsNone(self.clip.processed_asset_id)
        same, created = self.service.enqueue_clip(self.selection, self.asset, self.user)
        self.assertFalse(created)
        self.assertEqual(same.pk, self.clip.pk)
        self.assertEqual(PipelineJob.objects.count(), 1)
        attempt = PipelineJobService.claim_next()
        self.assertEqual(attempt.pk, self.job.pk)
        self.assertIsNone(PipelineJobService.claim_next())
        processed = self.service.process_job(attempt)
        self.assertEqual(len(self.ffmpeg.calls), 1)
        self.assertEqual(processed.status, SourceClip.Status.VALIDATED)
        self.assertEqual(processed.pipeline_job.status, PipelineJob.Status.SUCCEEDED)

    def test_heartbeat_extends_only_the_current_live_attempt(self):
        attempt = PipelineJobService.start(self.job)
        initial_expiry = attempt.lease_expires_at
        later = attempt.heartbeat_at + timedelta(seconds=5)
        with patch("production.services.jobs.timezone.now", return_value=later):
            refreshed = PipelineJobService.heartbeat(attempt)
        self.assertGreater(refreshed.lease_expires_at, initial_expiry)
        self.assertEqual(refreshed.lease_token, attempt.lease_token)
        self.expire(attempt)
        with self.assertRaises(PipelineJobStateError):
            PipelineJobService.heartbeat(attempt)

    def test_queue_and_clip_roll_back_together_when_artifact_creation_fails(self):
        with patch("production.services.clips.SourceClip.save", side_effect=RuntimeError("persistence failure")):
            with self.assertRaises(RuntimeError):
                self.service.enqueue_clip(self.selection, self.asset, self.user, padding_after=1)
        self.assertEqual(PipelineJob.objects.count(), 1)
        self.assertEqual(SourceClip.objects.count(), 1)
        self.assertEqual(self.ffmpeg.calls, [])

    def test_retry_cannot_collide_with_equivalent_queued_or_running_work(self):
        attempt = PipelineJobService.start(self.job)
        self.service._record_failure(self.clip, attempt, "temporary_error", "Try again.")
        replacement, created = self.service.enqueue_clip(self.selection, self.asset, self.user)
        self.assertTrue(created)
        self.assertNotEqual(replacement.pipeline_job_id, self.job.pk)
        with self.assertRaisesMessage(PipelineJobStateError, "Equivalent work"):
            PipelineJobService.retry(self.job, self.user)
        PipelineJobService.start(replacement.pipeline_job)
        with self.assertRaisesMessage(PipelineJobStateError, "Equivalent work"):
            PipelineJobService.retry(self.job, self.user)
        self.client.force_login(self.user)
        response = self.client.post(reverse("production:retry_job", args=[self.job.pk]))
        self.assertRedirects(response, reverse("production:project_jobs", args=[self.project.pk]))
        self.job.refresh_from_db()
        self.clip.refresh_from_db()
        self.assertEqual(self.job.status, PipelineJob.Status.FAILED)
        self.assertEqual(self.clip.status, SourceClip.Status.FAILED)
        self.assertEqual(PipelineJob.objects.filter(status__in=["queued", "running"]).count(), 1)

    def test_expired_attempt_cannot_write_before_or_after_recovery_and_new_claim(self):
        old = PipelineJobService.start(self.job)
        self.expire(old)
        for mutation in (
            lambda: PipelineJobService.succeed(old),
            lambda: PipelineJobService.fail(old),
            lambda: PipelineJobService.update_progress(old, 90),
        ):
            with self.assertRaises(PipelineJobStateError):
                mutation()
        self.assertEqual(PipelineJobService.recover_expired(), 1)
        replacement = PipelineJobService.claim_next()
        self.assertEqual(replacement.pk, old.pk)
        self.assertNotEqual(replacement.lease_token, old.lease_token)
        self.assertEqual(replacement.attempt_count, 2)
        for mutation in (
            lambda: PipelineJobService.succeed(old),
            lambda: PipelineJobService.fail(old),
            lambda: PipelineJobService.heartbeat(old),
        ):
            with self.assertRaises(PipelineJobStateError):
                mutation()
        current = self.service.process_job(replacement)
        with self.assertRaises(SourceClipError):
            self.service.process_job(old)
        current.refresh_from_db()
        current.processed_asset.refresh_from_db()
        self.assertEqual(current.status, SourceClip.Status.VALIDATED)
        self.assertEqual(current.processed_asset.status, MediaAsset.Status.VALIDATED)
        self.assertEqual(PipelineJob.objects.get(pk=old.pk).status, PipelineJob.Status.SUCCEEDED)

    def test_late_output_cannot_publish_over_a_replacement_attempt(self):
        old = PipelineJobService.start(self.job)
        original_create = self.service.media_service.create_generated_file
        replacements = []

        def finish_after_recovery(*args, **kwargs):
            output = original_create(*args, **kwargs)
            self.expire(old)
            self.assertEqual(PipelineJobService.recover_expired(), 1)
            replacements.append(PipelineJobService.claim_next())
            return output

        with patch.object(self.service.media_service, "create_generated_file", side_effect=finish_after_recovery):
            with self.assertRaises(SourceClipError):
                self.service.process_job(old)
        self.clip.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.clip.status, SourceClip.Status.PROCESSING)
        self.assertIsNone(self.clip.processed_asset_id)
        self.assertEqual(self.job.status, PipelineJob.Status.RUNNING)
        self.assertEqual(self.job.lease_token, replacements[0].lease_token)
        self.assertEqual(MediaAsset.objects.get(kind=MediaAsset.Kind.SOURCE_CLIP).status, MediaAsset.Status.FAILED)
        self.assertFalse(ArtifactDependency.objects.exists())
        result = self.service.process_job(replacements[0])
        self.assertEqual(result.status, SourceClip.Status.VALIDATED)
        self.assertEqual(result.pipeline_job.attempt_count, 2)

    def test_expired_recovery_stops_at_maximum_attempts(self):
        for number in range(1, self.job.max_attempts + 1):
            attempt = PipelineJobService.claim_next()
            self.assertEqual(attempt.attempt_count, number)
            self.expire(attempt)
            self.assertEqual(PipelineJobService.recover_expired(), 1)
        self.job.refresh_from_db()
        self.clip.refresh_from_db()
        self.assertEqual(self.job.status, PipelineJob.Status.FAILED)
        self.assertEqual(self.job.error_code, "worker_interrupted")
        self.assertEqual(self.clip.status, SourceClip.Status.FAILED)
        self.assertIsNone(PipelineJobService.claim_next())
        self.assertEqual(PipelineJobService.recover_expired(), 0)
        with self.assertRaises(PipelineJobStateError):
            PipelineJobService.retry(self.job, self.user)

    def test_recovery_retries_only_when_project_and_clip_inputs_remain_eligible(self):
        attempt = PipelineJobService.start(self.job)
        self.source.transcript_chunks.filter(sequence=1).update(text="Revised source")
        self.expire(attempt)
        self.assertEqual(PipelineJobService.recover_expired(), 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, PipelineJob.Status.FAILED)
        self.assertIsNone(PipelineJobService.claim_next())

    def test_cancellation_fences_inflight_output_and_is_not_recovered(self):
        attempt = PipelineJobService.start(self.job)
        original_create = self.service.media_service.create_generated_file

        def finish_after_cancel(*args, **kwargs):
            output = original_create(*args, **kwargs)
            PipelineJobService.cancel(attempt, self.user)
            return output

        with patch.object(self.service.media_service, "create_generated_file", side_effect=finish_after_cancel):
            with self.assertRaises(SourceClipError):
                self.service.process_job(attempt)
        self.job.refresh_from_db()
        self.clip.refresh_from_db()
        self.assertEqual(self.job.status, PipelineJob.Status.CANCELLED)
        self.assertEqual(self.clip.status, SourceClip.Status.FAILED)
        self.assertEqual(self.clip.error_code, "job_cancelled")
        self.assertIsNone(self.clip.processed_asset_id)
        self.assertFalse(ArtifactDependency.objects.exists())
        self.assertEqual(PipelineJobService.recover_expired(), 0)
        with self.assertRaises(PipelineJobStateError):
            PipelineJobService.succeed(attempt)

    def test_worker_once_claims_and_processes_exactly_one_job(self):
        second, _ = self.service.enqueue_clip(self.selection, self.asset, self.user, padding_after=1)
        output = io.StringIO()
        # A TestCase owns a surrounding transaction, which a long-running
        # management command must not close. The subprocess test uses real connections.
        with patch("production.management.commands.run_pipeline_worker.close_old_connections"), patch(
            "production.management.commands.run_pipeline_worker.SourceClipService", return_value=self.service
        ):
            call_command("run_pipeline_worker", once=True, stdout=output)
        self.job.refresh_from_db()
        second.pipeline_job.refresh_from_db()
        self.assertEqual(self.job.status, PipelineJob.Status.SUCCEEDED)
        self.assertEqual(second.pipeline_job.status, PipelineJob.Status.QUEUED)
        self.assertEqual(len(self.ffmpeg.calls), 1)
        self.assertIn("ready for review", output.getvalue())

    def test_job_routes_require_login_ownership_post_and_csrf(self):
        retry_url = reverse("production:retry_job", args=[self.job.pk])
        cancel_url = reverse("production:cancel_job", args=[self.job.pk])
        jobs_url = reverse("production:project_jobs", args=[self.project.pk])
        for url in (retry_url, cancel_url, jobs_url):
            self.assertEqual(self.client.get(url).status_code, 302)
        for url in (retry_url, cancel_url):
            self.assertEqual(self.client.post(url).status_code, 302)
        outsider = get_user_model().objects.create_user("worker-outsider")
        self.client.force_login(outsider)
        for url in (retry_url, cancel_url):
            self.assertEqual(self.client.post(url).status_code, 404)
        self.assertEqual(self.client.get(jobs_url).status_code, 404)
        self.client.force_login(self.user)
        for url in (retry_url, cancel_url):
            self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        for url in (retry_url, cancel_url):
            self.assertEqual(csrf_client.post(url).status_code, 403)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, PipelineJob.Status.QUEUED)

    def test_owner_and_staff_can_retry_failed_clips_and_cancel(self):
        staff = get_user_model().objects.create_user("worker-staff", is_staff=True)
        for retry_user, cancel_user in ((self.user, staff), (staff, self.user)):
            with self.subTest(retry_user=retry_user.username, cancel_user=cancel_user.username):
                clip, _ = self.service.enqueue_clip(self.selection, self.asset, self.user)
                attempt = PipelineJobService.start(clip.pipeline_job)
                self.service._record_failure(clip, attempt, "temporary_error", "Try again.")
                self.client.force_login(retry_user)
                response = self.client.post(reverse("production:retry_job", args=[attempt.pk]))
                self.assertRedirects(response, reverse("production:project_jobs", args=[self.project.pk]))
                attempt.refresh_from_db()
                clip.refresh_from_db()
                self.assertEqual(attempt.status, PipelineJob.Status.QUEUED)
                self.assertEqual(clip.status, SourceClip.Status.PROCESSING)
                self.client.force_login(cancel_user)
                response = self.client.post(reverse("production:cancel_job", args=[attempt.pk]))
                self.assertRedirects(response, reverse("production:project_jobs", args=[self.project.pk]))
                attempt.refresh_from_db()
                self.assertEqual(attempt.status, PipelineJob.Status.CANCELLED)


@skipUnless(shutil.which(settings.FFMPEG_EXECUTABLE) and shutil.which(settings.FFPROBE_EXECUTABLE),
            "FFmpeg and FFprobe are required for worker restart integration.")
class WorkerProcessRestartTests(SimpleTestCase):
    def test_abrupt_worker_exit_is_recovered_by_fresh_worker_with_real_media(self):
        workspace = Path(settings.BASE_DIR)
        with tempfile.TemporaryDirectory(prefix="youtube-worker-restart-") as temporary:
            directory = Path(temporary)
            fixture = directory / "fixture.mp4"
            fixture_result = subprocess.run(
                [settings.FFMPEG_EXECUTABLE, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10", "-t", "4",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(fixture)],
                capture_output=True, text=True, timeout=30, shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(fixture_result.returncode, 0, fixture_result.stderr)
            environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "YoutubeContent.settings",
                           "DJANGO_DB_PATH": str(directory / "worker.sqlite3"),
                           "DJANGO_MEDIA_ROOT": str(directory / "media"),
                           "DJANGO_DEBUG": "1", "PIPELINE_RETRY_DELAY_SECONDS": "0",
                           "FFMPEG_EXECUTABLE": str(shutil.which(settings.FFMPEG_EXECUTABLE)),
                           "FFPROBE_EXECUTABLE": str(shutil.which(settings.FFPROBE_EXECUTABLE)),
                           "WORKER_TEST_FIXTURE": str(fixture), "PYTHONDONTWRITEBYTECODE": "1"}

            def run_script(source, expected=0):
                result = subprocess.run(
                    [sys.executable, "-B", "-c", textwrap.dedent(source)], cwd=workspace,
                    env=environment, capture_output=True, text=True, timeout=60, shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                return result.stdout

            run_script("""
                import os
                from pathlib import Path
                import django
                django.setup()
                from django.core.management import call_command
                from django.core.files.uploadedfile import SimpleUploadedFile
                from django.db import connections
                from production.models import MediaAsset
                from production.services.clips import SourceClipService
                from production.services.media_assets import MediaAssetService
                from production.tests.helpers import create_selected_segment
                call_command('migrate', verbosity=0, interactive=False)
                user, project, source, selection = create_selected_segment('restart-owner')
                asset = MediaAssetService().create_upload(
                    project, SimpleUploadedFile('authorized.mp4', Path(os.environ['WORKER_TEST_FIXTURE']).read_bytes()),
                    user, rights_basis=MediaAsset.RightsBasis.USER_OWNED,
                    consent_metadata={'rights_confirmed': True})
                clip, created = SourceClipService().enqueue_clip(selection, asset, user)
                assert created and clip.pipeline_job.status == 'queued'
                connections.close_all()
            """)
            run_script("""
                import os
                import django
                django.setup()
                from production.services.jobs import PipelineJobService
                attempt = PipelineJobService.claim_next()
                assert attempt and attempt.attempt_count == 1 and attempt.lease_token
                # Simulate a hard process failure after its durable claim, without cleanup.
                os._exit(23)
            """, expected=23)
            run_script("""
                from datetime import timedelta
                import django
                django.setup()
                from django.db import connections
                from django.utils import timezone
                from production.models import PipelineJob, SourceClip
                job = PipelineJob.objects.get()
                assert job.status == 'running' and job.attempt_count == 1
                assert SourceClip.objects.get().processed_asset_id is None
                PipelineJob.objects.filter(pk=job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
                connections.close_all()
            """)
            worker = subprocess.run(
                [sys.executable, "-B", "manage.py", "run_pipeline_worker", "--once"],
                cwd=workspace, env=environment, capture_output=True, text=True, timeout=60, shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
            self.assertIn("Recovered 1 interrupted attempt", worker.stdout)
            self.assertIn("ready for review", worker.stdout)
            final_state = run_script("""
                import json
                import django
                django.setup()
                from django.db import connections
                from production.models import PipelineJob, SourceClip
                clip = SourceClip.objects.select_related('pipeline_job', 'processed_asset').get()
                print(json.dumps({'clip': clip.status, 'job': clip.pipeline_job.status,
                    'attempts': clip.pipeline_job.attempt_count, 'duration': float(clip.actual_duration_seconds),
                    'output_exists': clip.processed_asset.file.storage.exists(clip.processed_asset.file.name),
                    'running': PipelineJob.objects.filter(status='running').count()}))
                connections.close_all()
            """)
            state = json.loads(final_state)
            self.assertEqual((state["clip"], state["job"], state["attempts"]), ("validated", "succeeded", 2))
            self.assertTrue(state["output_exists"])
            self.assertEqual(state["running"], 0)
            self.assertAlmostEqual(state["duration"], 2.0, delta=0.35)
