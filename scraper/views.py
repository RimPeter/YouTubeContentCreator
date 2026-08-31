from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.core.management import call_command
from django.conf import settings
from io import StringIO
import json
import logging

logger = logging.getLogger(__name__)


def scraper_form(request):
    """Render the YouTube transcript scraper form."""
    return render(request, 'scraper/scrape_form.html')


def transcript_viewer(request):
    """Display the current transcript file."""
    output_file = settings.BASE_DIR / 'scraper' / 'transcript_output.json'
    
    if not output_file.exists():
        return JsonResponse({'error': 'No transcript file found'}, status=404)
    
    try:
        with open(output_file, 'r', encoding='utf-8') as f:
            transcript = json.load(f)
        
        return JsonResponse({
            'success': True,
            'count': len(transcript),
            'file_size': output_file.stat().st_size,
            'first_entry': transcript[0] if transcript else None,
            'last_entry': transcript[-1] if transcript else None,
        })
    except Exception as e:
        logger.exception(f"Error reading transcript: {e}")
        return JsonResponse({'error': str(e)}, status=500)


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
        
        # Write URL to file for the management command to read
        url_file = settings.BASE_DIR / 'scraper' / 'youtube_url.txt'
        url_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(url_file, 'w', encoding='utf-8') as f:
            f.write(url)
        
        logger.info(f"[API] URL written to: {url_file}")
        
        # Capture command output
        out = StringIO()
        err = StringIO()
        try:
            logger.info("[API] Calling fetch_transcript command...")
            call_command('fetch_transcript', stdout=out, stderr=err)
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
        
        # Verify output file exists and has size
        output_file = settings.BASE_DIR / 'scraper' / 'transcript_output.json'
        if output_file.exists():
            file_size = output_file.stat().st_size
            logger.info(f"[API] Output file size: {file_size} bytes")
        else:
            logger.error(f"[API] Output file does not exist: {output_file}")
        
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
