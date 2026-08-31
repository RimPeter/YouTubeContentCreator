from django.contrib import admin
from django.urls import include, path
from users.views import dashboard, profile
from scraper.views import scraper_form, fetch_transcript_api, transcript_viewer

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("accounts/profile/", profile, name="profile"),
    path("scraper/", scraper_form, name="scraper_form"),
    path("scraper/fetch/", fetch_transcript_api, name="fetch_transcript_api"),
    path("scraper/view/", transcript_viewer, name="transcript_viewer"),
]