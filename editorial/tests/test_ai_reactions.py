import json
from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from editorial.models import ReactionBlock
from editorial.services.access import EditorialServiceError
from editorial.services.ai_reactions import AIReactionService, OpenAIReactionProvider
from editorial.services.reactions import deterministic_fallback, validate_provider_result
from production.models import PipelineJob
from production.services.jobs import PipelineJobService
from .helpers import EditorialTestCase, create_ready_package


def response_for(result):
    return {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": json.dumps(result)}]}]}


@override_settings(OPENAI_API_KEY='test-secret')
class AIReactionProviderTests(SimpleTestCase):
    @patch('editorial.services.ai_reactions.urlopen')
    def test_structured_request_and_response(self, open_url):
        open_url.return_value.__enter__.return_value.read.return_value = json.dumps(response_for({'test': 'draft'})).encode()
        self.assertEqual(OpenAIReactionProvider('test-model').generate({'question': 'test'}), {'test': 'draft'})
        request = open_url.call_args.args[0]
        body = json.loads(request.data)
        self.assertTrue(body['text']['format']['strict'])
        self.assertFalse(body['store'])
        self.assertEqual(body['model'], 'test-model')
        self.assertNotIn('test-secret', request.data.decode())
        self.assertEqual(open_url.call_args.kwargs['timeout'], 180)

    @patch('editorial.services.ai_reactions.urlopen')
    def test_malformed_incomplete_refused_and_oversized_responses(self, open_url):
        for raw in (b'not json', b'{"status":"incomplete"}', b'[]', b'x' * 2_000_001,
                    json.dumps({'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'refusal', 'refusal': 'sensitive detail'}]}]}).encode()):
            with self.subTest(raw=raw[:30]):
                open_url.return_value.__enter__.return_value.read.return_value = raw
                with self.assertRaises(EditorialServiceError) as error:
                    OpenAIReactionProvider('test').generate({})
                self.assertNotIn('sensitive detail', str(error.exception))

    @patch('editorial.services.ai_reactions.urlopen')
    def test_provider_errors_are_safe(self, open_url):
        for code, expected in ((401, 'authentication'), (429, 'quota'), (500, 'provider returned an error')):
            open_url.side_effect = HTTPError('https://example.com', code, 'secret detail', {}, None)
            with self.assertRaisesMessage(EditorialServiceError, expected):
                OpenAIReactionProvider('test').generate({})


@override_settings(OPENAI_API_KEY='test-secret', OPENAI_REACTION_MODEL='test-model')
class AIReactionWorkflowTests(EditorialTestCase):
    def setUp(self):
        self.user, self.project, _, _, self.clip, self.package = create_ready_package('ai-script-owner')
        self.client.force_login(self.user)
        self.url = reverse('editorial:generate_reaction', args=[self.package.pk])
        self.activity = reverse('editorial:research_detail', args=[self.package.pk]) + '?activity=reaction'

    def provider(self):
        provider = MagicMock(provider='openai', model='test-model')
        provider.generate.return_value = deterministic_fallback(self.package)
        return provider

    def test_queue_progress_complete_and_reuse(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        with patch.object(OpenAIReactionProvider, 'generate') as network:
            self.assertEqual(self.client.post(self.url).status_code, 302)
            network.assert_not_called()
        job = PipelineJob.objects.get(job_type='ai_reaction')
        self.assertEqual(AIReactionService.enqueue(self.package, self.user).pk, job.pk)
        self.assertContains(self.client.get(self.activity), 'Waiting for the background worker')
        provider = self.provider()
        def during_call(payload):
            job.refresh_from_db()
            self.assertEqual(job.progress, 10)
            self.assertContains(self.client.get(self.activity), 'Waiting for the AI provider')
            self.assertEqual(payload['verified_evidence_data'], [])
            return deterministic_fallback(self.package)
        provider.generate.side_effect = during_call
        block = AIReactionService.process_job(PipelineJobService.claim_next(), provider)
        job.refresh_from_db()
        self.assertEqual((job.status, job.progress), ('succeeded', 100))
        self.assertEqual(block.status, 'draft')
        self.assertFalse(block.used_fallback)
        self.assertEqual(block.provider_model, 'test-model')
        self.assertTrue(block.claims.exists())
        self.assertIsNone(block.approved_at)
        response = self.client.get(self.activity)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertContains(response, reverse('editorial:reaction_detail', args=[block.pk]))
        self.assertEqual(AIReactionService.enqueue(self.package, self.user).pk, job.pk)
        self.assertEqual(self.package.reaction_blocks.count(), 1)

    def test_failure_is_visible_and_retry_creates_real_draft(self):
        job = AIReactionService.enqueue(self.package, self.user)
        provider = self.provider()
        provider.generate.side_effect = RuntimeError('secret response')
        with self.assertRaises(EditorialServiceError):
            AIReactionService.process_job(PipelineJobService.start(job), provider)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertNotIn('secret', job.error_message)
        self.assertFalse(self.package.reaction_blocks.filter(status='draft').exists())
        self.assertContains(self.client.get(self.activity), 'Script generation failed')
        PipelineJobService.retry(job, self.user)
        block = AIReactionService.process_job(PipelineJobService.start(job), self.provider())
        self.assertEqual(block.version, 2)

    def test_cancel_input_change_and_expired_lease_discard_output(self):
        for change in ('cancel', 'inputs', 'lease', 'actor'):
            with self.subTest(change=change):
                job = AIReactionService.enqueue(self.package, self.user)
                provider = self.provider()
                def during_call(payload):
                    if change == 'cancel': PipelineJobService.cancel(job, self.user)
                    if change == 'inputs':
                        type(self.package).objects.filter(pk=self.package.pk).update(editorial_focus='Changed focus')
                    if change == 'lease':
                        PipelineJob.objects.filter(pk=job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
                    if change == 'actor':
                        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
                    return deterministic_fallback(self.package)
                provider.generate.side_effect = during_call
                with self.assertRaises(EditorialServiceError):
                    AIReactionService.process_job(PipelineJobService.start(job), provider)
                self.assertFalse(self.package.reaction_blocks.filter(status='draft').exists())
                get_user_model().objects.filter(pk=self.user.pk).update(is_active=True)
                # A lost lease remains for the worker's recovery loop to handle.
                PipelineJob.objects.filter(pk=job.pk, status='running').update(status='failed')

    def test_invalid_citations_never_save_a_draft(self):
        job = AIReactionService.enqueue(self.package, self.user)
        provider = self.provider()
        provider.generate.return_value['claims'][0]['evidence_source_id'] = 999999
        with self.assertRaises(EditorialServiceError):
            AIReactionService.process_job(PipelineJobService.start(job), provider)
        self.assertFalse(self.package.reaction_blocks.filter(status='draft').exists())
        for invalid in (True, [], '1'):
            raw = deterministic_fallback(self.package)
            raw['claims'][0]['transcript_sequence'] = invalid
            with self.assertRaises(EditorialServiceError):
                validate_provider_result(self.package, raw)

    def test_job_and_draft_completion_are_atomic(self):
        job = AIReactionService.enqueue(self.package, self.user)
        with patch.object(PipelineJobService, 'succeed', side_effect=RuntimeError('commit failure')):
            with self.assertRaises(EditorialServiceError):
                AIReactionService.process_job(PipelineJobService.start(job), self.provider())
        self.assertFalse(self.package.reaction_blocks.filter(status='draft').exists())
        self.assertFalse(self.package.reaction_blocks.filter(claims__isnull=False).exists())

    def test_worker_dispatches_ai_reaction(self):
        job = AIReactionService.enqueue(self.package, self.user)
        with patch('production.management.commands.run_pipeline_worker.close_old_connections'), patch.object(
            PipelineJobService, 'maintain_lease', return_value=nullcontext()
        ), patch.object(OpenAIReactionProvider, 'generate', return_value=deterministic_fallback(self.package)):
            call_command('run_pipeline_worker', once=True)
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')

    def test_access_csrf_and_ready_research_required(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url).status_code, 403)
        other = get_user_model().objects.create_user('script-other')
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.activity).status_code, 404)
        self.package.status = 'draft'
        self.package.save(update_fields=['status'])
        with self.assertRaises(EditorialServiceError):
            AIReactionService.enqueue(self.package, self.user)

    @override_settings(OPENAI_API_KEY='')
    def test_no_key_blocks_ai_but_basic_draft_remains_available(self):
        self.client.post(self.url)
        self.assertFalse(PipelineJob.objects.filter(job_type='ai_reaction').exists())
        response = self.client.post(self.url, {'mode': 'basic'}, follow=True)
        self.assertContains(response, 'deterministic transcript-only fallback')
        self.assertTrue(ReactionBlock.objects.get().used_fallback)
