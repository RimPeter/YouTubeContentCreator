from django.urls import path


app_name = "editorial"

from . import views

urlpatterns = [
    path("projects/<int:project_pk>/", views.project_editorial, name="project_editorial"),
    path("clips/<int:clip_pk>/research/create/", views.create_research, name="create_research"),
    path("research/<int:package_pk>/", views.research_detail, name="research_detail"),
    path("research/<int:package_pk>/evidence/add/", views.add_evidence, name="add_evidence"),
    path("evidence/<int:evidence_pk>/verify/", views.verify_evidence, name="verify_evidence"),
    path("evidence/<int:evidence_pk>/reject/", views.reject_evidence, name="reject_evidence"),
    path("evidence/<int:evidence_pk>/delete/", views.delete_evidence, name="delete_evidence"),
    path("research/<int:package_pk>/ready/", views.mark_research_ready, name="mark_research_ready"),
    path("research/<int:package_pk>/reaction/generate/", views.generate_reaction, name="generate_reaction"),
    path("reactions/<int:block_pk>/", views.reaction_detail, name="reaction_detail"),
    path("reactions/<int:block_pk>/edit/", views.edit_reaction, name="edit_reaction"),
    path("reactions/<int:block_pk>/claims/add/", views.add_claim, name="add_claim"),
    path("claims/<int:claim_pk>/delete/", views.delete_claim, name="delete_claim"),
    path("reactions/<int:block_pk>/approve/", views.approve_reaction, name="approve_reaction"),
]
