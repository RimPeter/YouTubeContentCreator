from django.shortcuts import render


def dashboard(request):
    """Display the main landing dashboard for the creator app."""
    projects = []
    stats = {
        "projects": len(projects),
        "drafts": 0,
        "ready_to_render": 0,
        "queued_jobs": 0,
    }

    return render(
        request,
        "users/dashboard.html",
        {
            "projects": projects,
            "stats": stats,
            "page_title": "Creator Dashboard",
        },
    )
