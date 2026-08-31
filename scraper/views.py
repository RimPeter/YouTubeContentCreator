from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.http import require_http_methods, require_POST
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from io import StringIO
import json
import logging

from .models import VideoProject, SourceVideo, TranscriptChunk
from .forms import VideoProjectForm, SourceVideoForm, QuickCreateProjectForm
from .services import TranscriptService, TranscriptExtractionError, TranscriptFetchError, TranscriptStorageError

logger = logging.getLogger(__name__)


# Legacy views (kept for backward compatibility)

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


# New CRUD views

@login_required
def project_list(request):
    """List all projects for the current user."""
    projects = VideoProject.objects.all().order_by('-updated_at')
    context = {
        'projects': projects,
        'total_projects': projects.count(),
    }
    return render(request, 'scraper/project_list.html', context)


@login_required
def project_create(request):
    """Create a new project with a YouTube URL."""
    if request.method == 'POST':
        form = QuickCreateProjectForm(request.POST)
        if form.is_valid():
            try:
                # Create project
                project = VideoProject.objects.create(
                    title=form.cleaned_data['project_title'],
                    status='draft'
                )
                
                url = form.cleaned_data['youtube_url']
                
                # Extract video ID
                try:
                    video_id = TranscriptService.extract_video_id(url)
                except TranscriptExtractionError as e:
                    project.delete()
                    messages.error(request, f"Invalid YouTube URL: {e}")
                    return redirect('scraper_project_create')
                
                # Create source video
                source_video = SourceVideo.objects.create(
                    project=project,
                    youtube_url=url,
                    youtube_video_id=video_id,
                    title='[Auto-fetched]',
                    channel='[Auto-fetched]'
                )
                
                messages.success(request, f"Project '{project.title}' created successfully!")
                return redirect('scraper_project_detail', pk=project.pk)
            
            except Exception as e:
                logger.exception(f"Error creating project: {e}")
                messages.error(request, f"Error creating project: {str(e)}")
    else:
        form = QuickCreateProjectForm()
    
    context = {'form': form}
    return render(request, 'scraper/project_create.html', context)


@login_required
def project_detail(request, pk):
    """View project details and manage transcript."""
    project = get_object_or_404(VideoProject, pk=pk)
    source_video = project.source_video
    chunks = source_video.transcript_chunks.all().order_by('sequence') if source_video else None
    
    context = {
        'project': project,
        'source_video': source_video,
        'chunks': chunks,
        'chunk_count': chunks.count() if chunks else 0,
    }
    return render(request, 'scraper/project_detail.html', context)


@login_required
@require_POST
def project_fetch_transcript(request, pk):
    """Fetch transcript for a project."""
    project = get_object_or_404(VideoProject, pk=pk)
    source_video = project.source_video
    
    if not source_video:
        messages.error(request, "This project has no source video.")
        return redirect('scraper_project_detail', pk=project.pk)
    
    try:
        result = TranscriptService.process_video(source_video)
        messages.success(
            request,
            f"Transcript fetched successfully! {result['chunks_saved']} chunks saved."
        )
    except Exception as e:
        logger.exception(f"Error fetching transcript: {e}")
        messages.error(request, f"Error fetching transcript: {str(e)}")
    
    return redirect('scraper_project_detail', pk=project.pk)


@login_required
def project_transcript_viewer(request, pk):
    """View transcript chunks for a project."""
    project = get_object_or_404(VideoProject, pk=pk)
    source_video = project.source_video
    
    if not source_video:
        messages.error(request, "This project has no source video.")
        return redirect('scraper_project_detail', pk=project.pk)
    
    chunks = source_video.transcript_chunks.all().order_by('sequence')
    
    context = {
        'project': project,
        'source_video': source_video,
        'chunks': chunks,
    }
    return render(request, 'scraper/transcript_viewer.html', context)


@login_required
@require_POST
def project_approve_transcript(request, pk):
    """Mark transcript as approved."""
    project = get_object_or_404(VideoProject, pk=pk)
    source_video = project.source_video
    
    if not source_video:
        messages.error(request, "This project has no source video.")
        return redirect('scraper_project_detail', pk=project.pk)
    
    if source_video.transcript_fetched:
        project.status = 'needs_review'
        project.save()
        messages.success(request, "Transcript marked as ready for review.")
    else:
        messages.warning(request, "No transcript to approve.")
    
    return redirect('scraper_project_detail', pk=project.pk)


@login_required
@require_POST
def project_lock(request, pk):
    """Lock a completed project."""
    project = get_object_or_404(VideoProject, pk=pk)
    
    project.status = 'locked'
    project.save()
    messages.success(request, "Project locked.")
    
    return redirect('scraper_project_detail', pk=project.pk)


@login_required
@require_POST
def project_delete(request, pk):
    """Delete a project."""
    project = get_object_or_404(VideoProject, pk=pk)
    title = project.title
    project.delete()
    messages.success(request, f"Project '{title}' deleted.")
    return redirect('scraper_project_list')


@login_required
@require_POST
def project_archive(request, pk):
    """Archive a project (set status to locked without deleting)."""
    project = get_object_or_404(VideoProject, pk=pk)
    project.status = 'locked'
    project.save()
    messages.success(request, f"Project '{project.title}' archived.")
    return redirect('scraper_project_list')


# API endpoints

@login_required
@require_http_methods(["GET"])
def api_project_chunks(request, pk):
    """API endpoint to get transcript chunks for a project."""
    project = get_object_or_404(VideoProject, pk=pk)
    source_video = project.source_video
    
    if not source_video:
        return JsonResponse({'error': 'No source video'}, status=404)
    
    chunks = source_video.transcript_chunks.all().order_by('sequence').values(
        'id', 'sequence', 'start', 'duration', 'text', 'processed', 'segment_type'
    )
    
    return JsonResponse({
        'success': True,
        'project_id': project.id,
        'chunk_count': len(list(chunks)),
        'chunks': list(chunks)
    })


@login_required
@require_http_methods(["POST"])
def api_project_export_transcript(request, pk):
    """API endpoint to export transcript to JSON."""
    project = get_object_or_404(VideoProject, pk=pk)
    source_video = project.source_video
    
    if not source_video:
        return JsonResponse({'error': 'No source video'}, status=404)
    
    try:
        export_path = TranscriptService.export_to_json(source_video)
        return JsonResponse({
            'success': True,
            'export_path': str(export_path),
            'file_size': export_path.stat().st_size,
        })
    except Exception as e:
        logger.exception(f"Error exporting transcript: {e}")
        return JsonResponse({'error': str(e)}, status=500)
