from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST
from django.core.management import call_command
from io import StringIO
import json
import logging

from .models import ScrapedVideo

logger = logging.getLogger(__name__)


def scraper_form(request):
    """Render the YouTube transcript scraper form."""
    scraped_videos = ScrapedVideo.objects.order_by('-created_at')[:5]
    return render(request, 'scraper/scrape_form.html', {
        'scraped_videos': scraped_videos,
    })


def scraped_video_list(request):
    """Display all saved scraped videos."""
    return render(request, 'scraper/scraped_video_list.html', {
        'scraped_videos': ScrapedVideo.objects.all(),
    })


def transcript_detail(request, pk):
    """Display one saved transcript in chronological order."""
    scraped_video = get_object_or_404(ScrapedVideo, pk=pk)
    return render(request, 'scraper/transcript_detail.html', {
        'scraped_video': scraped_video,
        'entries': scraped_video.entries.all(),
    })


@require_POST
def delete_transcript(request, pk):
    """Delete a saved transcript and all of its entries."""
    scraped_video = get_object_or_404(ScrapedVideo, pk=pk)
    scraped_video.delete()
    return redirect('scraped_video_list')


@require_http_methods(["POST"])
def fetch_transcript_api(request):
    """API endpoint to fetch transcript from a YouTube URL."""
    try:
        data = json.loads(request.body)
        url = data.get('url', '').strip()
        
        logger.info(f"[API] Received URL: {url}")
        
        if not url:
            logger.warning("[API] No URL provided")
            return JsonResponse({'error': 'URL is required'}, status=400)
        
        video_id = extract_video_id(url)
        if not video_id:
            return JsonResponse({'error': 'Please enter a valid YouTube URL.'}, status=400)
        if ScrapedVideo.objects.filter(youtube_video_id=video_id).exists():
            return JsonResponse({'error': 'This video has already been scraped.'}, status=409)
        
        # Capture command output
        out = StringIO()
        err = StringIO()
        try:
            logger.info("[API] Calling fetch_transcript command...")
            call_command('fetch_transcript', '--url', url, stdout=out, stderr=err)
            logger.info("[API] Command completed successfully")
        except Exception as cmd_err:
            error_msg = f"Command error: {str(cmd_err)}\n{err.getvalue()}"
            logger.error(f"[API] {error_msg}")
            return JsonResponse({'error': error_msg}, status=500)
        
        output = out.getvalue().strip()
        error_output = err.getvalue().strip()
        
        logger.info(f"[API] Command output: {output}")
        
        if error_output:
            logger.error(f"[API] Command stderr: {error_output}")
            return JsonResponse({'error': error_output}, status=500)
        
        return JsonResponse({
            'success': True,
            'message': output
        })
    except json.JSONDecodeError as e:
        logger.error(f"[API] JSON decode error: {e}")
        return JsonResponse({'error': 'Invalid JSON in request'}, status=400)
    except Exception as e:
        logger.exception(f"[API] Unexpected error: {e}")
        return JsonResponse({'error': f'Unexpected error: {str(e)}'}, status=500)


def extract_video_id(url):
    """Extract a video ID from the YouTube URL formats the command supports."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    if parsed.netloc in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
        if parsed.path == '/watch':
            return parse_qs(parsed.query).get('v', [None])[0]
        if parsed.path.startswith('/shorts/') or parsed.path.startswith('/embed/'):
            return parsed.path.split('/')[2]
    if parsed.netloc == 'youtu.be':
        return parsed.path.strip('/').split('/')[0]
    return None
