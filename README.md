# Creator Studio

CRUD corrections: creator timeline turns support up to 10,002 characters (reaction plus bridge). Timeline forms carry a revision token; outdated submissions display a conflict page preserving the submitted text. Editing a timeline recalculates recording readiness and invalidates affected assemblies. Selections require a positive duration. Draft research and evidence can be corrected; changed evidence must be verified again. Use **Create editable revision** for reviewed plans/research, preserving the originals. Recordings can be explicitly selected, deselected, or have approval revoked without deleting history. Staff can reassign imported projects through the project page; transfers and editorial changes are recorded in read-only workflow audit records. Historical derived artifacts use revision/exclusion rather than hard deletion.

Reaction Timelines now have a recording review screen. Review the included script to mark it ready for recording, upload narration, listen to takes, and explicitly approve/select one take per creator turn. Text, order, inclusion or trim edits require another script review; matching recordings remain available. The screen shows duration estimates, missing media, and assembly history. A handoff assembly saves exact media references, checksums, ranges, durations and transcript text; downloads use its saved script. Older assemblies without these snapshots remain stale history. Video rendering is a later step.

A Django workspace for reviewing YouTube transcripts, selecting segments, producing clips from authorized source media, collecting evidence, and approving reaction scripts. Project owners see their own work; staff can review all projects.

The dashboard shows current reviews, active jobs, failed job history, and the next available step. When an OpenAI API key is configured, analysis groups transcripts into topic-aware segments; otherwise it uses clearly marked deterministic sections. AI web research and reaction-script generation are also available with an OpenAI API key; a basic local reaction draft is available. Final video assembly and publishing are not connected in the application.

## Local setup

Use Python 3.12 or later (CI uses 3.13), FFmpeg, and FFprobe. The Python packages are pinned in `requirements.txt`. Both media executables must be on `PATH`, or set `FFMPEG_EXECUTABLE` and `FFPROBE_EXECUTABLE` to their absolute paths.

Windows PowerShell, from the repository directory:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B manage.py init_local_settings
.\.venv\Scripts\python.exe -B manage.py migrate
.\.venv\Scripts\python.exe -B manage.py createsuperuser
.\.venv\Scripts\python.exe -B manage.py check_production_health
.\.venv\Scripts\python.exe -B manage.py runserver
```

Open `http://127.0.0.1:8000/`. Existing installations can use their existing virtual environment, including `23082026\Scripts\python.exe`, instead of creating another one. On macOS/Linux use `python3 -m venv .venv` and `.venv/bin/python` for the subsequent commands.

In a second terminal, run the worker with the same virtual environment and configuration:

```powershell
.\.venv\Scripts\python.exe -B manage.py run_pipeline_worker --poll-interval 2
```

`init_local_settings` creates an ignored `.local-secret-key` and preserves it on later runs. Keep it private; replacing it invalidates signed sessions. Settings never write files during import. Until the command is run, development uses a temporary key per process, so signed sessions will reset on restart. Development defaults to local hosts, a local SQLite database, local media, and console email. Account emails appear in the web process terminal.

## Using and revising a project

1. Create a project, add a YouTube URL with an available transcript, and review the saved text. Approve the project to enable analysis.
2. Run analysis and select segments. Review the start/end times and order. A locked project must be unlocked before further work.
3. Upload source video with the required rights confirmation, then request a clip for each selection. The request queues durable work. Follow **Jobs** for progress and review the clip when processing finishes.
4. Approve a validated clip, create research, add and verify evidence, and mark the research ready.
   Use **Suggest research directions** beside the research form to get three editable question/focus pairs based on the selected segment's transcript. Suggestions use local topic rules and transcript excerpts, with general claim-checking prompts for other topics. They do not search the web or verify claims. Reviewing suggestions does not save a package; choose **Create research package with this direction** when ready.
5. On ready research, choose **Generate AI reaction draft** and follow Script activity. Open **Review generated script** when it completes, edit its sections and claims, review evidence and originality, then approve it. **Start with a basic draft instead** creates a local template without API usage.
6. On an approved reaction, upload an audio-only narration recording. The app validates WAV, MP3, M4A, or AAC media, measures duration, and keeps it attached to the exact approved script. Playback is available only to users with project access.

Changing a selection or source invalidates affected clips and editorial work. **Stale** means an input changed and the result must be regenerated before approval. Deselection retires the selection, preserving its clip history. Reselecting creates a new active selection. Historical versions remain accessible for review; approved history cannot be edited in admin. To switch to a new analysis run for a source, retire its previous selections first. Transcript approval protects the source text; create another project when a different transcript is required.

## Worker operation and recovery

The database-backed worker handles `clip_trim`, `ai_research`, `ai_reaction`, `transcript_analysis`, and `sequence_reaction` jobs. AI transcript analysis and continuous reaction scripts return immediately to Jobs, which refreshes while work is active and links to completed results. Deterministic analysis and basic editorial drafts still run synchronously. Web and worker processes must share the same database, media directory, and configuration. Restart the worker after code changes; it does not auto-reload. Start with one worker for a local SQLite installation; SQLite serializes writes and is intended here for a single-host deployment. Do not put the database on a network filesystem.

Apply migrations before starting the updated application: `python manage.py migrate`. The additive `production.0006_pipeline_job_result` migration stores job result references. Repeated submissions reuse active analysis jobs; after completion, **Run new analysis** deliberately creates a new run, including after credentials are corrected. Continuous script requests reuse active or successful work for the same plan content and model. Changed inputs, cancellation, or an expired lease prevent late results from being published. New analysis jobs create their analysis record only when results are ready to commit, so interrupted work remains visible in Jobs without leaving a permanently running analysis record.

Analysis fallback messages distinguish authentication, quota, connectivity, timeout, and invalid-output failures. Authentication and quota failures are not retried within an attempt. Diagnostic logging includes only source IDs, error categories, and attempt numbers. A fallback is always identified on both the result and Jobs pages.

Media copies occur before the database write transaction. The attachment transaction rechecks project access, lifecycle state, and any worker lease; failed attachment removes the staged file. An abrupt process termination during the copy can still leave an unreferenced storage file, so database/media backups remain necessary. Workflow indicators summarize current artifact versions; historical versions remain available in stage listings. Dashboard guidance follows the current plan, script, recording, and handoff chain.

```text
python -B manage.py run_pipeline_worker --poll-interval 2
python -B manage.py run_pipeline_worker --once
python -B manage.py run_pipeline_worker --max-jobs 10
```

`--once` makes one polling/claim attempt and exits; `--max-jobs` bounds how many jobs a worker processes. Use a process supervisor to restart the long-running worker after failure. Queued jobs remain durable while no worker is running.

Each running attempt has a lease and heartbeat. The worker recovers expired leases on subsequent polls, automatically requeues eligible interrupted work up to the configured attempt limit, and checks attempt ownership before publishing results. Other processing failures remain visible for review and manual retry. Default timings are a 30-second lease, 5-second heartbeat, and 5-second recovery retry delay. Keep the heartbeat interval comfortably shorter than the lease. FFmpeg and FFprobe also have bounded execution times.

On a failure, open the project's **Jobs** page, inspect the error, fix the cause (for example, an unavailable media tool), then retry eligible work. Cancel queued or running work from the same page. If inputs changed, regenerate from the current selection rather than retrying an obsolete result. After a worker interruption, restart it and allow the old lease to expire. Do not manually reset job statuses or modify approved artifact rows in admin.

## Production configuration

### AI web research

Configure `OPENAI_API_KEY` in the environments used by Django and the worker, then restart both. Keep the key private; do not enter it into a form or commit it. `.env` files are not loaded automatically. `OPENAI_RESEARCH_MODEL` defaults to `gpt-6-astra` and can be set to another Responses API model supporting web search that your API account can access.

### AI transcript analysis

`OPENAI_ANALYSIS_MODEL` defaults to the research model and may be set to a Responses API model that supports Structured Outputs. On an approved, unlocked project, **Run new analysis** sends ordered timestamped transcript chunks to the provider and asks it to create contiguous conversation-topic segments. Each segment is validated for complete transcript coverage, chronological boundaries, text limits, and valid scores before it is saved. If the provider is unavailable, refuses, or produces invalid output after the configured attempts, the analysis is saved as a clearly marked deterministic fallback. The request does not send video or local files, and the API response is not stored by OpenAI (`store: false`).

For local development, the ignored root `env.py` file can alternatively contain `OPENAI_API_KEY=...`, `OPENAI_RESEARCH_MODEL=...`, and `OPENAI_REACTION_MODEL=...` entries (quoted values also work). Only those three settings are read as data; Python code in that file is never executed. Explicit environment variables take precedence. Production ignores this file. Restart the server and worker after changing it.

On a draft research package, click **Run AI research**. The worker sends the question, editorial focus, and selected segment transcript to OpenAI's [Responses API with web search](https://developers.openai.com/api/docs/guides/tools-web-search). It requests a report with primary sources, counterevidence, and limitations. API usage charges apply. No video file, local file path, or API key is stored in the job payload. Each provider request has a 180-second network timeout and a 6,000 output-token cap; these are not a total cost limit. An interrupted job may repeat the provider request during recovery (at most two attempts per job).

Research activity updates automatically every three seconds. Open the saved report when complete to read its clickable citations and evidence leads. AI leads are marked **Unverified** and **Context** until reviewed; AI completion does not mark the package ready or approve a reaction. Duplicate requests reuse queued, running, or successful work for the same inputs. Failed jobs can be retried from **Jobs**. Create a new research version for a fresh investigation after a successful run. Missing credentials, incomplete or uncited responses, and provider errors produce visible errors rather than fabricated fallback research. Automated tests use mocked provider responses; live API access must be checked with your configured account.

### AI reaction scripts

`OPENAI_REACTION_MODEL` defaults to the configured research model; optionally set it to a model your account can access that supports Responses API structured outputs. The worker uses [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) to request script sections and traceable claims from the selected transcript and human-verified evidence. It makes no web searches and sends no media files. Each attempt has a 180-second network timeout, a 6,000 output-token cap, and one provider request. Research payloads larger than 100,000 serialized characters are rejected before enqueueing.

Script activity reports queue, generation, validation, completion, and failure stages. Duplicate requests reuse the same job and successful draft for unchanged inputs/model; edit that draft or create new research for a fresh version. Failed jobs can be retried from **Jobs**, up to two attempts total. Cancelling, losing the worker lease, or changing inputs prevents late output from becoming a usable draft. API failures do not silently substitute a basic draft. Validated drafts remain unapproved until human review; citation validation checks references, not whether evidence supports every sentence.

### Deployment settings

`.env.example` lists the supported environment variables. Django does **not** load `.env` automatically: export variables in your shell or configure your process manager/container to load them. Use the same environment for the web process, worker, migrations, and management commands.

Production requires `DJANGO_ENV=production`, `DJANGO_DEBUG=false`, a new external `DJANGO_SECRET_KEY`, explicit `DJANGO_ALLOWED_HOSTS`, and `DJANGO_SMTP_HOST`. Do not reuse the secret previously committed in this repository. Generate a replacement with `python -c "import secrets; print(secrets.token_urlsafe(64))"` and store it in your deployment's secret store. Configure the SMTP credentials and sender address, then verify actual delivery before opening signups.

Serve the Django WSGI application `YoutubeContent.wsgi:application` with a production server behind HTTPS. The deployment must provide and supervise that server; the development `runserver` is for local use. Collect static assets and configure the reverse proxy to serve `DJANGO_STATIC_ROOT` at `/static/`. Media is private: keep `DJANGO_MEDIA_ROOT` outside public static hosting and use the authenticated media routes.

Secure session/CSRF cookies and HTTPS redirects are enabled when debug is off. Set `DJANGO_TRUST_PROXY_HTTPS=true` only when your trusted reverse proxy removes client-supplied forwarding headers and sets `X-Forwarded-Proto` itself. Otherwise leave it false.

HSTS defaults to one hour in production. Subdomain coverage and preload are deliberately opt-in: enable `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` and `DJANGO_SECURE_HSTS_PRELOAD` only after confirming that the entire domain can require HTTPS. Without those choices, Django reports `security.W005` and `security.W021`; review the domain's deployment policy before enabling them. CI uses an isolated example domain with both enabled for the strict deployment check. See [Django's deployment checklist](https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/) for the infrastructure checks these settings cannot perform.

Before deploying code or migrations:

```text
python -B manage.py check --deploy --fail-level WARNING
python -B manage.py check_production_health
python -B manage.py migrate --plan
python -B manage.py migrate --noinput
python -B manage.py collectstatic --noinput
```

Run these with the production environment supplied, after a backup and migration rehearsal. Environment controls include `DJANGO_DB_PATH`, `DJANGO_MEDIA_ROOT`, and `DJANGO_STATIC_ROOT` so rehearsals can operate on copies. This repository provides configuration and checks; no production deployment is performed automatically.

## Backups, migrations, and cleanup

Back up the database and media together before upgrades. Stop the web process and worker so they cannot write while copying, record the deployed code revision and environment configuration, and copy `DJANGO_DB_PATH` plus the entire `DJANGO_MEDIA_ROOT` into a private backup directory. Keep production secrets in the deployment secret store. When using SQLite WAL mode, use SQLite's backup API or copy all database sidecar files with every process stopped; copying only a live main database file is unsafe.

Restore the backup into separate directories first, set the three path environment variables to those directories, run `migrate --plan`, `migrate`, `check`, and the media health check, then inspect representative projects and play a clip. Run the worker against only this rehearsal copy. Once validated, stop live processes, make a fresh coordinated backup, apply the migration to the live database, and restart web and worker processes. Never point a rehearsal at production media.

To roll back a failed upgrade, stop all writers and restore the matching database, media, code revision, and configuration from the coordinated backup. Do not assume that reversing a data migration recreates retired selections or historical artifacts. Review any writes since the backup before restoring it.

The cleanup command defaults to a dry run:

```text
python -B manage.py cleanup_media_assets --older-than-days 30
```

Review candidates and back up before adding `--execute`. Cleanup only targets eligible, unreferenced failed/superseded assets; it does not replace a retention policy for approved history.

The local database, media, collected static files, virtual environments, bytecode, logs, and local secrets are ignored by Git. Their local copies are not deployment artifacts.

## Verification and documentation

```text
python -B manage.py check
python -B manage.py makemigrations --check --dry-run
python -B manage.py check_production_health
python -B manage.py test --noinput
```

The Django test runner uses an isolated test database; media-producing tests use temporary storage. Set `DJANGO_TEST_DB_PATH` to a separate disposable database file to exercise SQLite concurrency tests across connections; never use the live database path. CI uses this file-backed test configuration. FFmpeg integration tests require both executables. CI installs them, verifies migrations, runs the suite, checks synthetic production configuration, and collects static files. Live YouTube availability and real SMTP/provider delivery require separate checks with the actual deployment services.

This README is the current operating guide. `PHASE_A_VERIFICATION_REPORT.md`, the `PHASE_B` through `PHASE_E_IMPLEMENTATION_REPORT.md` files, and `CONSOLIDATION_REPORT.md` are historical implementation records. Their old test counts, limitations, synchronous processing descriptions, and setup notes describe the code at the time; use the current services, migrations, tests, and this guide for present behavior.
