# Phase B implementation report

Date: 2026-08-31

## Outcome

Phase B passes its gate. Transcript analysis and segment selection are implemented in the new `analysis` Django app without changing canonical transcript content or adding Phase C+ production features.

The app can create versioned analysis runs, validate structured provider output, retry bounded provider failures, fall back deterministically, calculate transparent weighted scores, detect stale runs, and persist an authorized ordered segment selection across a multi-source project.

## App boundary

The existing `scraper` app remains responsible for projects, source videos, YouTube ingestion, and canonical transcript chunks.

The new `analysis` app owns:

- `AnalysisRun`
- `AnalysisSegment`
- `SegmentSelection`
- transcript fingerprinting
- structured analysis-output validation
- scoring and deterministic fallback segmentation
- analysis versioning and stale detection
- selection, deselection, editing, and ordering
- analysis forms, views, URLs, templates, and admin
- Phase B tests

The only scraper UI change is a project-detail link to its analysis dashboard.

## Migrations

- `analysis.0001_initial` creates the three Phase B models, indexes, relationships, ordering rules, and core uniqueness/check constraints.
- `analysis.0002_add_boundary_constraints` adds portable non-negative segment/selection boundary constraints.

Both migrations are additive. The migration test applies and reverses the analysis migrations while proving existing scraper project, source, and transcript rows remain intact.

## Analysis behavior

- Analysis requires an approved, unlocked, non-archived project, a completed transcript, and an authorized owner/staff user.
- The source fingerprint is SHA-256 over ordered chunk IDs, sequence, timing, and text.
- Reanalysis allocates a new per-source version and retains previous runs and segments.
- Provider metadata, prompt/model identifiers, algorithm version, configuration, timestamps, fallback use, and sanitized failure state are stored.
- Provider output must cover every non-empty chunk exactly once in ordered, non-overlapping segments.
- Boundaries must reference valid source chunk sequences.
- Labels and text fields have validated type/count/length limits.
- Scores must provide exactly relevance, clarity, factual density, novelty, controversy, reaction potential, and clip suitability on a finite 0–100 scale.
- Configured weights must be finite, non-negative, and sum to 1.0. The aggregate is stored to three decimal places.
- Provider calls are injectable and retried one to three times according to validated configuration.
- Invalid/unavailable provider output uses deterministic chunk-group fallback segmentation with an explicit warning and neutral scores.
- Segment persistence is atomic. A persistence failure leaves no partial segments and marks the run failed with a sanitized error.

No live AI provider adapter or credential was added. The current UI deliberately uses deterministic fallback segmentation; a future provider adapter can be injected into `AnalysisService` without changing persistence or validation rules.

## Selection behavior

- Selection requires the same approved, unlocked project lifecycle and owner/staff authorization.
- Only successful, non-stale analysis segments belonging to the project may be selected.
- Reviewed start/end overrides must be supplied together and remain inside the segment.
- Selection is idempotent and supports notes and boundary updates.
- Project-wide selection order is unique and contiguous after deselection/reordering.
- Different source videos may contribute selections to one project.
- A source cannot mix selected segments from different analysis-run versions; old selections must be cleared explicitly before switching that source to a newer run.
- Sorting analysis results for display never mutates persisted selection order.

## UI

- Project analysis dashboard with all sources, run history, success/failure/fallback/stale state, and ordered selected segments.
- POST-only analysis generation with CSRF protection.
- Analysis-run detail with source boundaries, text, summary, labels, score components, aggregate, and rationale.
- Source-order and score sorting.
- POST-only segment selection/update, deselection, and project-wide reordering.
- Owner scoping, explicit staff behavior, cross-user 404 responses, lifecycle messaging, and sanitized provider failures.

## Data reconciliation

Before and after Phase B schema application:

- Canonical projects: 2
- Canonical source videos: 2
- Canonical transcript chunks: 633
- Legacy videos: 1
- Legacy transcript entries: 254

After schema application, before any user-triggered analysis:

- Analysis runs: 0
- Analysis segments: 0
- Segment selections: 0

No existing data was copied, altered, or deleted by Phase B.

## Verification

- `python manage.py makemigrations --check` — passed; no model drift
- `python manage.py migrate` — passed; no pending migrations after application
- `python manage.py migrate --plan` — passed; no planned operations
- `python manage.py check` — passed; no issues
- Analysis app suite — 19 tests passed
- Full repository suite — 44 tests passed in 65.168 seconds
- Clean verification environment — 19 analysis tests passed in 30.046 seconds
- `git diff --check` — no whitespace errors; Windows line-ending notices only

Coverage includes models, constraints, migration forward/reverse behavior, fingerprints, provider validation, gaps, overlaps, bad scores, retries, fallback, sanitized failures, transaction rollback, reanalysis, stale detection, selection ordering/version guards, service- and view-level authorization, lifecycle restrictions, POST enforcement, CSRF, UI integration, and the complete transcript-to-selection workflow.

## Deferred work and next gate

- No live AI/LLM provider integration or secret handling was added.
- No capture, research, reaction writing, TTS, visual asset, job queue, FFmpeg, timeline, rendering, or publishing model/service was added.
- Deterministic fallback scores are neutral editorial placeholders and require human review.
- Background jobs are not needed for the current deterministic implementation; provider latency must be reassessed before enabling a live provider in normal HTTP requests.

Gate B: **PASS**

Next permitted work is Phase C: production artifact and job foundation. Do not begin automated media acquisition or rendering until Phase C storage, validation, idempotency, dependency, subprocess, and job-safety gates pass.
