from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from editorial.admin import ClaimInline, EvidenceInline
from editorial.models import EvidenceSource, ReactionBlock, ReactionClaim, ResearchPackage
from editorial.services.reactions import ReactionService
from editorial.services.research import ResearchService

from .helpers import EditorialTestCase, create_approved_clip


class EditorialAdminTests(EditorialTestCase):
    def setUp(self):
        self.user, self.project, _, _, self.clip = create_approved_clip("admin-editorial-owner")
        self.staff = get_user_model().objects.create_superuser("editorial-superuser", password="password")
        self.client.force_login(self.staff)
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.staff
        self.package = ResearchService.create_package(
            self.clip, self.user, research_question="Question", editorial_focus="Focus"
        )
        self.evidence = ResearchService.add_evidence(
            self.package, self.user, source_url="https://example.com/evidence", title="Report",
            retrieved_on="2026-09-01", classification="supports", finding="Finding", relevance="Relevant",
        )
        ResearchService.set_verification(self.evidence, self.user, verified=True)
        ResearchService.mark_ready(self.package, self.user)
        self.block = ReactionService().generate(self.package, self.user)
        self.claim = self.block.claims.get()

    def test_admin_artifacts_allow_inspection_but_deny_mutation_and_deletion(self):
        for obj in (self.package, self.evidence, self.block, self.claim):
            with self.subTest(model=obj._meta.model_name):
                model_admin = admin.site._registry[type(obj)]
                self.assertTrue(model_admin.has_view_permission(self.request, obj))
                self.assertFalse(model_admin.has_add_permission(self.request))
                self.assertFalse(model_admin.has_change_permission(self.request, obj))
                self.assertFalse(model_admin.has_delete_permission(self.request, obj))
                self.assertNotIn("delete_selected", model_admin.get_actions(self.request))
                url = reverse(f"admin:editorial_{obj._meta.model_name}_change", args=[obj.pk])
                self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.post(url, {"status": "approved", "text": "Changed"}).status_code, 403)
                delete_url = reverse(f"admin:editorial_{obj._meta.model_name}_delete", args=[obj.pk])
                self.assertEqual(self.client.post(delete_url, {"post": "yes"}).status_code, 403)
                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())
        self.claim.refresh_from_db()
        self.assertNotEqual(self.claim.text, "Changed")
        self.block.refresh_from_db()
        self.assertEqual(self.block.status, ReactionBlock.Status.DRAFT)

    def test_inline_and_bulk_paths_cannot_modify_editorial_history(self):
        for inline, parent, obj in (
            (EvidenceInline, ResearchPackage, self.package),
            (ClaimInline, ReactionBlock, self.block),
        ):
            model_admin = inline(parent, admin.site)
            self.assertFalse(model_admin.has_add_permission(self.request, obj))
            self.assertFalse(model_admin.has_change_permission(self.request, obj))
            self.assertFalse(model_admin.has_delete_permission(self.request, obj))
            self.assertEqual(
                set(model_admin.get_readonly_fields(self.request, obj)),
                {field.name for field in model_admin.model._meta.fields},
            )
        for model in (ResearchPackage, EvidenceSource, ReactionBlock, ReactionClaim):
            url = reverse(f"admin:editorial_{model._meta.model_name}_changelist")
            before = model.objects.count()
            self.client.post(url, {"action": "delete_selected", "_selected_action": list(model.objects.values_list("pk", flat=True)), "post": "yes"})
            self.assertEqual(model.objects.count(), before)

    def test_approved_reaction_and_claim_remain_read_only(self):
        ReactionService.approve(self.block, self.user, evidence_reviewed=True, originality_confirmed=True)
        self.block.refresh_from_db()
        self.assertFalse(admin.site._registry[ReactionBlock].has_change_permission(self.request, self.block))
        self.assertFalse(admin.site._registry[ReactionClaim].has_delete_permission(self.request, self.claim))
