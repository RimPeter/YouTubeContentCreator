# YouTube Content Creator

A Django-based pipeline for creating YouTube reaction videos with transcript analysis and AI-powered segmentation.

## Project Status

Currently at proof-of-concept stage with transcript fetching capabilities. Core data models and project management features are in development.

## Prerequisites

- Python 3.12+
- pip
- Virtual environment

## Quick Start

### 1. Set up Virtual Environment

```bash
# On Windows
python -m venv 23082026
.\23082026\Scripts\Activate.ps1

# On macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Configuration

Copy `.env.example` to `.env` and update with your settings:

```bash
cp .env.example .env
```

Edit `.env` with your configuration:
```
DEBUG=True
DJANGO_SECRET_KEY=your-secret-key-here-change-in-production
```

### 4. Database Setup

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 5. Run Development Server

```bash
python manage.py runserver
```

Visit `http://localhost:8000/` and log in to the admin at `/admin/`

## Project Structure

```
YouTubeContentCreator/
├── YoutubeContent/        # Project configuration
├── users/                 # User management app
├── scraper/               # YouTube transcript scraping
├── templates/             # HTML templates
├── static/                # CSS, JavaScript
├── manage.py              # Django management
└── requirements.txt       # Python dependencies
```

## Features

- **User Authentication**: Django admin + allauth
- **Transcript Fetching**: YouTube transcript API integration
- **Scraper Interface**: Web form and API endpoints

## Next Development Steps

See `youtube_content_creator_repository_review_next_steps.txt` for detailed roadmap.

### Immediate Priority

1. ✅ Repository cleanup and configuration
2. ⏳ Create core database models (VideoProject, SourceVideo, TranscriptChunk)
3. ⏳ Refactor transcript fetching into a service
4. ⏳ Build project CRUD views

## Commands

### Fetch Transcript (Management Command)

```bash
python manage.py fetch_transcript
```

This reads a YouTube URL from `scraper/youtube_url.txt` and saves the transcript.

### Common Django Commands

```bash
# Run migrations
python manage.py migrate

# Create migrations
python manage.py makemigrations

# Run tests
python manage.py test

# Access admin panel
python manage.py runserver
# Then visit http://localhost:8000/admin/
```

## Development

### Add Packages

```bash
pip install <package-name>
pip freeze > requirements.txt
```

### Troubleshooting

**Module not found errors**: Ensure virtual environment is activated and dependencies are installed.

```bash
pip install -r requirements.txt
```

## License

[Add your license here]
