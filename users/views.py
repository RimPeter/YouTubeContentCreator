from django.shortcuts import render
from django.contrib.auth.decorators import login_required


def dashboard(request):
    """Render the main dashboard/homepage."""
    return render(request, 'users/dashboard.html')


@login_required(login_url='account_login')
def profile(request):
    """Render the user profile page."""
    return render(request, 'users/profile.html', {'user': request.user})
