from django.contrib import admin
from django.urls import include, path
from users.views import dashboard, profile
from scraper.views import (
    delete_transcript,
    fetch_transcript_api,
    scraped_video_list,
    scraper_form,
    transcript_detail,
)

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("accounts/profile/", profile, name="profile"),
    path("scraper/", scraper_form, name="scraper_form"),
    path("scraper/fetch/", fetch_transcript_api, name="fetch_transcript_api"),
    path("scraper/list/", scraped_video_list, name="scraped_video_list"),
    path("scraper/<int:pk>/delete/", delete_transcript, name="delete_transcript"),
    path("scraper/<int:pk>/", transcript_detail, name="transcript_detail"),
]