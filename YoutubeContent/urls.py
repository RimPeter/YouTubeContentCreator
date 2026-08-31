from django.contrib import admin
from django.urls import include, path
from users.views import dashboard, profile
from scraper.views import (
    # Legacy views
    scraper_form, fetch_transcript_api, transcript_viewer,
    # New CRUD views
    project_list, project_create, project_detail, project_fetch_transcript,
    project_transcript_viewer, project_approve_transcript, project_lock, project_delete,
    project_archive, api_project_chunks, api_project_export_transcript
)

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("accounts/profile/", profile, name="profile"),
    
    # Legacy scraper routes
    path("scraper/", scraper_form, name="scraper_form"),
    path("scraper/fetch/", fetch_transcript_api, name="fetch_transcript_api"),
    path("scraper/view/", transcript_viewer, name="transcript_viewer"),
    
    # Project CRUD routes
    path("projects/", project_list, name="scraper_project_list"),
    path("projects/create/", project_create, name="scraper_project_create"),
    path("projects/<int:pk>/", project_detail, name="scraper_project_detail"),
    path("projects/<int:pk>/fetch-transcript/", project_fetch_transcript, name="scraper_project_fetch"),
    path("projects/<int:pk>/transcript/", project_transcript_viewer, name="scraper_transcript_view"),
    path("projects/<int:pk>/approve/", project_approve_transcript, name="scraper_project_approve"),
    path("projects/<int:pk>/lock/", project_lock, name="scraper_project_lock"),
    path("projects/<int:pk>/delete/", project_delete, name="scraper_project_delete"),
    path("projects/<int:pk>/archive/", project_archive, name="scraper_project_archive"),
    
    # API routes
    path("api/projects/<int:pk>/chunks/", api_project_chunks, name="api_project_chunks"),
    path("api/projects/<int:pk>/export/", api_project_export_transcript, name="api_project_export"),
]