from django.urls import path


app_name = "editorial"

from . import views

urlpatterns = [
    path("reaction-assemblies/<int:assembly_pk>/script/", views.assembly_script_download, name="assembly_script_download"),
    path("reaction-timelines/<int:timeline_pk>/recording/", views.recording_review, name="recording_review"),
    path("reaction-timelines/<int:timeline_pk>/recording/ready/", views.review_recording_script, name="review_recording_script"),
    path("reaction-timelines/<int:timeline_pk>/script/", views.recording_script_download, name="recording_script_download"),
    path("timeline-narrations/<int:take_pk>/media/", views.timeline_take_media, name="timeline_take_media"),
    path("projects/<int:project_pk>/", views.project_editorial, name="project_editorial"),
    path("projects/<int:project_pk>/reaction-plan/", views.create_reaction_plan, name="create_reaction_plan"),
    path("reaction-plans/<int:plan_pk>/", views.reaction_plan_detail, name="reaction_plan_detail"),
    path("reaction-plans/<int:plan_pk>/edit/", views.edit_reaction_plan, name="edit_reaction_plan"),
    path("reaction-plans/<int:plan_pk>/ready/", views.ready_reaction_plan, name="ready_reaction_plan"),
    path("reaction-plans/<int:plan_pk>/draft/generate/", views.generate_reaction_sequence_draft, name="generate_reaction_sequence_draft"),
    path("reaction-sequence-drafts/<int:draft_pk>/", views.reaction_sequence_draft_detail, name="reaction_sequence_draft_detail"),
    path("reaction-sequence-drafts/<int:draft_pk>/timeline/create/", views.create_reaction_timeline, name="create_reaction_timeline"),
    path("reaction-timelines/<int:timeline_pk>/", views.reaction_timeline_detail, name="reaction_timeline_detail"),
    path("reaction-timelines/<int:timeline_pk>/creator/add/", views.add_timeline_creator_item, name="add_timeline_creator_item"),
    path("reaction-timeline-items/<int:item_pk>/creator/edit/", views.edit_timeline_creator_item, name="edit_timeline_creator_item"),
    path("reaction-timeline-items/<int:item_pk>/source/edit/", views.edit_timeline_source_item, name="edit_timeline_source_item"),
    path("reaction-timeline-items/<int:item_pk>/move/", views.move_timeline_item, name="move_timeline_item"),
    path("reaction-timeline-items/<int:item_pk>/narration/upload/", views.upload_timeline_narration, name="upload_timeline_narration"),
    path("timeline-narrations/<int:take_pk>/approve/", views.approve_timeline_narration, name="approve_timeline_narration"),
    path("reaction-timelines/<int:timeline_pk>/assembly/create/", views.create_reaction_assembly, name="create_reaction_assembly"),
    path("reaction-assemblies/<int:assembly_pk>/", views.reaction_assembly_detail, name="reaction_assembly_detail"),
    path("reaction-plan-sections/<int:section_pk>/edit/", views.edit_reaction_plan_section, name="edit_reaction_plan_section"),
    path("clips/<int:clip_pk>/research/create/", views.create_research, name="create_research"),
    path("clips/<int:clip_pk>/research/suggestions/", views.research_suggestions, name="research_suggestions"),
    path("research/<int:package_pk>/", views.research_detail, name="research_detail"),
    path("research/<int:package_pk>/ai/", views.run_ai_research, name="run_ai_research"),
    path("research/<int:package_pk>/evidence/add/", views.add_evidence, name="add_evidence"),
    path("evidence/<int:evidence_pk>/verify/", views.verify_evidence, name="verify_evidence"),
    path("evidence/<int:evidence_pk>/reject/", views.reject_evidence, name="reject_evidence"),
    path("evidence/<int:evidence_pk>/delete/", views.delete_evidence, name="delete_evidence"),
    path("research/<int:package_pk>/ready/", views.mark_research_ready, name="mark_research_ready"),
    path("research/<int:package_pk>/reaction/generate/", views.generate_reaction, name="generate_reaction"),
    path("reactions/<int:block_pk>/", views.reaction_detail, name="reaction_detail"),
    path("reactions/<int:block_pk>/narration/upload/", views.upload_narration, name="upload_narration"),
    path("reactions/<int:block_pk>/edit/", views.edit_reaction, name="edit_reaction"),
    path("reactions/<int:block_pk>/claims/add/", views.add_claim, name="add_claim"),
    path("claims/<int:claim_pk>/delete/", views.delete_claim, name="delete_claim"),
    path("reactions/<int:block_pk>/approve/", views.approve_reaction, name="approve_reaction"),
]
