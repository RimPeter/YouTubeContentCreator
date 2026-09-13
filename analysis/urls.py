from django.urls import path

from . import views


app_name = "analysis"

urlpatterns = [
    path("projects/<int:project_pk>/", views.project_dashboard, name="project_dashboard"),
    path("sources/<int:source_pk>/run/", views.run_analysis, name="run_analysis"),
    path("runs/<int:run_pk>/", views.run_detail, name="run_detail"),
    path("segments/<int:segment_pk>/select/", views.select_segment, name="select_segment"),
    path("segments/<int:segment_pk>/exclude/", views.exclude_segment, name="exclude_segment"),
    path(
        "selections/<int:selection_pk>/delete/",
        views.deselect_segment,
        name="deselect_segment",
    ),
    path(
        "projects/<int:project_pk>/selections/reorder/",
        views.reorder_selections,
        name="reorder_selections",
    ),
]
