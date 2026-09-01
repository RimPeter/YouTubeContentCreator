from django.contrib import admin
from django.urls import include, path

from scraper import views as scraper_views
from users.views import dashboard, profile


urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("accounts/profile/", profile, name="profile"),
    path("analysis/", include("analysis.urls")),
    path("production/", include("production.urls")),
    path("editorial/", include("editorial.urls")),
    path("projects/", scraper_views.project_list, name="project_list"),
    path("projects/create/", scraper_views.project_create, name="project_create"),
    path("projects/<int:pk>/", scraper_views.project_detail, name="project_detail"),
    path("projects/<int:pk>/edit/", scraper_views.project_update, name="project_update"),
    path("projects/<int:pk>/ingest/", scraper_views.project_ingest, name="project_ingest"),
    path("projects/<int:pk>/approve/", scraper_views.project_approve, name="project_approve"),
    path("projects/<int:pk>/lock/", scraper_views.project_lock, name="project_lock"),
    path("projects/<int:pk>/unlock/", scraper_views.project_unlock, name="project_unlock"),
    path("projects/<int:pk>/archive/", scraper_views.project_archive, name="project_archive"),
    path("projects/<int:pk>/delete/", scraper_views.project_delete, name="project_delete"),
    path("transcripts/", scraper_views.source_video_list, name="source_video_list"),
    path("transcripts/<int:pk>/", scraper_views.transcript_detail, name="transcript_detail"),
    path("transcripts/<int:pk>/delete/", scraper_views.delete_transcript, name="delete_transcript"),
    path("scraper/", scraper_views.scraper_form, name="scraper_form"),
    path("scraper/fetch/", scraper_views.fetch_transcript_api, name="fetch_transcript_api"),
    path("scraper/list/", scraper_views.scraped_video_list, name="scraped_video_list"),
    path("scraper/<int:pk>/", scraper_views.transcript_detail),
    path("scraper/<int:pk>/delete/", scraper_views.delete_transcript),
    # Historical project URL names retained as reverse-lookup aliases.
    path("projects/", scraper_views.project_list, name="scraper_project_list"),
    path("projects/create/", scraper_views.project_create, name="scraper_project_create"),
    path("projects/<int:pk>/", scraper_views.project_detail, name="scraper_project_detail"),
    path("projects/<int:pk>/fetch-transcript/", scraper_views.project_ingest, name="scraper_project_fetch"),
    path("projects/<int:pk>/transcript/", scraper_views.project_detail, name="scraper_transcript_view"),
    path("projects/<int:pk>/approve/", scraper_views.project_approve, name="scraper_project_approve"),
    path("projects/<int:pk>/lock/", scraper_views.project_lock, name="scraper_project_lock"),
    path("projects/<int:pk>/delete/", scraper_views.project_delete, name="scraper_project_delete"),
    path("projects/<int:pk>/archive/", scraper_views.project_archive, name="scraper_project_archive"),
]
