import json
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from editorial.services.ai_research import AIResearchService, OpenAIResearchProvider, parse_response, render_report
from editorial.services.access import EditorialServiceError
from editorial.services.research import ResearchService
from production.models import PipelineJob
from production.services.jobs import PipelineJobService
from .helpers import EditorialTestCase, create_ready_package


def api_response():
    text = 'Independent findings support part of the claim. [source]'
    return {"status": "completed", "output": [
        {"type": "web_search_call", "status": "completed"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text,
         "annotations": [{"type": "url_citation", "start_index": text.index('[source]'),
                          "end_index": len(text), "url": "https://example.com/report", "title": "Source report"}]}]},
    ]}


class ResearchProviderTests(SimpleTestCase):
    def test_report_keeps_citations_and_escapes_model_html(self):
        report = parse_response(api_response())
        report['text'] += '<script>alert(1)</script>'
        rendered = str(render_report(report))
        self.assertIn('href="https://example.com/report"', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertIn('Independent findings', report['citations'][0]['finding'])

    def test_incomplete_uncited_and_unsafe_responses_rejected(self):
        for change in ('status', 'search', 'citations', 'url', 'offset'):
            response = api_response()
            citation = response['output'][1]['content'][0]['annotations'][0]
            if change == 'status': response['status'] = 'incomplete'
            if change == 'search': response['output'].pop(0)
            if change == 'citations': response['output'][1]['content'][0]['annotations'] = []
            if change == 'url': citation['url'] = 'http://127.0.0.1/private'
            if change == 'offset': citation['end_index'] = 99999
            with self.subTest(change=change), self.assertRaises((ValueError, EditorialServiceError)):
                parse_response(response)

    @override_settings(OPENAI_API_KEY='test-only-secret')
    @patch('editorial.services.ai_research.urlopen')
    def test_request_requires_web_search_and_does_not_store_response(self, open_url):
        open_url.return_value.__enter__.return_value.read.return_value = json.dumps(api_response()).encode()
        OpenAIResearchProvider().research({'question': 'Question'}, 'test-model')
        request = open_url.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body['tools'], [{'type': 'web_search'}])
        self.assertEqual(body['tool_choice'], 'required')
        self.assertFalse(body['store'])
        self.assertNotIn('test-only-secret', request.data.decode())


@override_settings(OPENAI_API_KEY='test-only-secret', OPENAI_RESEARCH_MODEL='test-model')
class AIResearchWorkflowTests(EditorialTestCase):
    def setUp(self):
        self.user, self.project, _, _, self.clip, self.package = create_ready_package('ai-research-owner')
        self.package.status = 'draft'
        self.package.save(update_fields=['status'])
        self.client.force_login(self.user)

    def provider(self):
        provider = MagicMock()
        provider.research.return_value = parse_response(api_response())
        return provider

    def test_enqueue_claim_complete_and_render_with_unverified_evidence(self):
        url = reverse('editorial:run_ai_research', args=[self.package.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        job = PipelineJob.objects.get(job_type='ai_research')
        self.assertEqual(AIResearchService.enqueue(self.package, self.user).pk, job.pk)
        attempt = PipelineJobService.claim_next()
        self.assertEqual(attempt.pk, job.pk)
        AIResearchService.process_job(attempt, self.provider())
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')
        evidence = self.package.evidence_sources.get()
        self.assertEqual(evidence.source_origin, 'provider')
        self.assertEqual(evidence.verification_status, 'unverified')
        response = self.client.get(reverse('editorial:research_detail', args=[self.package.pk]))
        self.assertContains(response, 'AI research report')
        self.assertContains(response, 'https://example.com/report')
        self.assertEqual(AIResearchService.enqueue(self.package, self.user).pk, job.pk)

    def test_worker_dispatches_research_jobs(self):
        from django.core.management import call_command
        job = AIResearchService.enqueue(self.package, self.user)
        from contextlib import nullcontext
        # This dispatch test runs inside TestCase's transaction; the real worker
        # owns its connections and is covered separately by TransactionTestCase.
        with patch('production.management.commands.run_pipeline_worker.close_old_connections'), patch.object(PipelineJobService, 'maintain_lease', return_value=nullcontext()), patch.object(
            OpenAIResearchProvider, 'research', return_value=parse_response(api_response())
        ):
            call_command('run_pipeline_worker', once=True)
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')

    def test_activity_tracks_worker_and_completion_without_exposing_other_projects(self):
        url = reverse('editorial:research_detail', args=[self.package.pk]) + '?activity=1'
        job = AIResearchService.enqueue(self.package, self.user)
        response = self.client.get(url)
        self.assertContains(response, 'Waiting for the background worker')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotContains(response, '<form')
        provider = self.provider()

        def while_researching(*args):
            job.refresh_from_db()
            self.assertEqual(job.progress, 10)
            response = self.client.get(url)
            self.assertContains(response, 'Waiting for the research provider')
            self.assertContains(response, 'Last worker heartbeat')
            return parse_response(api_response())

        provider.research.side_effect = while_researching
        AIResearchService.process_job(PipelineJobService.start(job), provider)
        self.assertContains(self.client.get(url), 'Research complete')
        other = get_user_model().objects.create_user('activity-other')
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_cancelled_or_changed_inputs_discard_late_output(self):
        for change in ('cancel', 'ready', 'question', 'actor'):
            with self.subTest(change=change):
                job = AIResearchService.enqueue(self.package, self.user)
                attempt = PipelineJobService.start(job)
                provider = self.provider()
                def change_during_call(*args):
                    if change == 'cancel': PipelineJobService.cancel(job, self.user)
                    if change == 'ready': ResearchService.mark_ready(self.package, self.user)
                    if change == 'question':
                        type(self.package).objects.filter(pk=self.package.pk).update(research_question='Changed')
                    if change == 'actor':
                        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
                    return parse_response(api_response())
                provider.research.side_effect = change_during_call
                with self.assertRaises(EditorialServiceError):
                    AIResearchService.process_job(attempt, provider)
                self.assertFalse(self.package.evidence_sources.exists())
                self.package.status = 'draft'
                self.package.save(update_fields=['status'])
                get_user_model().objects.filter(pk=self.user.pk).update(is_active=True)

    def test_provider_failure_does_not_create_fake_evidence_and_can_retry(self):
        job = AIResearchService.enqueue(self.package, self.user)
        provider = self.provider()
        provider.research.side_effect = RuntimeError('sensitive provider detail')
        with self.assertRaisesMessage(EditorialServiceError, 'AI research failed'):
            AIResearchService.process_job(PipelineJobService.start(job), provider)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertNotIn('sensitive', job.error_message)
        self.assertFalse(self.package.evidence_sources.exists())
        PipelineJobService.retry(job, self.user)
        AIResearchService.process_job(PipelineJobService.start(job), self.provider())
        self.assertEqual(self.package.evidence_sources.count(), 1)

    @override_settings(OPENAI_API_KEY='')
    def test_missing_key_creates_no_job(self):
        with self.assertRaisesMessage(EditorialServiceError, 'OPENAI_API_KEY'):
            AIResearchService.enqueue(self.package, self.user)
        self.assertFalse(PipelineJob.objects.filter(job_type='ai_research').exists())

    def test_owner_and_csrf_boundaries(self):
        url = reverse('editorial:run_ai_research', args=[self.package.pk])
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(url).status_code, 403)
        other = get_user_model().objects.create_user('ai-research-other')
        self.client.force_login(other)
        self.assertEqual(self.client.post(url).status_code, 404)
