from django.urls import path

from . import views


app_name = "production"

urlpatterns = [
    path("projects/<int:project_pk>/jobs/", views.project_jobs, name="project_jobs"),
    path("jobs/<int:job_pk>/retry/", views.retry_job, name="retry_job"),
    path("jobs/<int:job_pk>/cancel/", views.cancel_job, name="cancel_job"),
    path("projects/<int:project_pk>/clips/", views.project_clips, name="project_clips"),
    path(
        "projects/<int:project_pk>/source-media/upload/",
        views.upload_source_media,
        name="upload_source_media",
    ),
    path(
        "selections/<int:selection_pk>/clips/create/",
        views.create_source_clip,
        name="create_source_clip",
    ),
    path("clips/<int:clip_pk>/", views.clip_detail, name="clip_detail"),
    path(
        "clips/<int:clip_pk>/approve/",
        views.approve_source_clip,
        name="approve_source_clip",
    ),
    path(
        "clips/<int:clip_pk>/media/",
        views.stream_source_clip,
        name="stream_source_clip",
    ),
]
