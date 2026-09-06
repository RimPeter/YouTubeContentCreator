from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from analysis.services import AnalysisService, SelectionService
from .helpers import create_approved_source


@override_settings(ALLOWED_HOSTS=["testserver"])
class AnalysisAdminProtectionTests(TestCase):
    def test_analysis_and_selection_history_is_inspectable_but_immutable(self):
        user, project, source, _chunks = create_approved_source("analysis-admin-owner")
        run = AnalysisService().analyze(source, user)
        segment = run.segments.first()
        selection, _ = SelectionService.select(project, segment, user)
        administrator = get_user_model().objects.create_superuser("analysis-admin", "admin@example.com", "password")
        self.client.force_login(administrator)
        for obj in (run, segment, selection):
            prefix = f"admin:analysis_{obj._meta.model_name}"
            with self.subTest(model=obj._meta.model_name):
                self.assertEqual(self.client.get(reverse(f"{prefix}_change", args=[obj.pk])).status_code, 200)
                self.assertEqual(self.client.post(reverse(f"{prefix}_change", args=[obj.pk]), {"reviewed_start_seconds": 1, "title": "Tampered"}).status_code, 403)
                self.assertEqual(self.client.post(reverse(f"{prefix}_delete", args=[obj.pk]), {"post": "yes"}).status_code, 403)
                self.client.post(reverse(f"{prefix}_changelist"), {"action": "delete_selected", "_selected_action": [obj.pk], "post": "yes"})
                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())
        selection.refresh_from_db()
        self.assertIsNone(selection.reviewed_start_seconds)
