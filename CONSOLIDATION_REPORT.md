# Phase 1 consolidation report

## Outcome

`VideoProject -> SourceVideo -> TranscriptChunk` is the canonical runtime hierarchy. A project owns multiple source videos, project ownership controls access, and transcript ingestion is centralized in `TranscriptService`. The transitional `ScrapedVideo` and `TranscriptEntry` tables remain intact for the compatibility window and are not used by runtime views, commands, services, or admin.

Phase 2 analysis work has not started.

## Canonical decisions

- `VideoProject.owner` is required and protected from account-deletion cascades.
- `SourceVideo.project` is a many-to-one foreign key.
- `(project, youtube_video_id)` is unique; the same video may be used in another project.
- `(source_video, sequence)` is unique and chunk sequence/timing values are non-negative.
- Approval/archive status and `is_locked` are separate. Approved content is immutable, lock/unlock is explicit, and archive is terminal in Phase 1.
- Imported rows retain `legacy_scraped_video_id`; import warnings/errors are durable `LegacyImportConflict` rows.

## Migrations

- `0004_restore_canonical_schema`: additive creation of canonical and conflict tables, constraints, and indexes. Reversing this migration drops canonical tables and is safe only before runtime cutover/new canonical writes.
- `0005_import_legacy_history`: imports schema-valid legacy rows, timestamps, titles, timing, ordering, and identity. It creates the inactive `__legacy_transcript_import__` owner and the `Imported transcript history` project. Reverse removes only provenance-marked sources/chunks, its conflict rows, and its empty imported project. It deliberately retains the inactive reserved account because historical third-party user relations make automatic deletion unsafe.

The migration is idempotent by legacy provenance. Invalid identity/timing is retained in legacy tables and recorded as a durable error instead of guessed or discarded. Blank titles use the video ID and produce an auditable warning.

## Local reconciliation on 2026-08-31

Before import:

- `ScrapedVideo`: 1
- `TranscriptEntry`: 254

After import:

- Legacy rows: 1 video / 254 entries, unchanged
- Imported projects: 1
- Imported source videos: 1
- Imported transcript chunks: 254
- Import conflicts: 0
- Imported source sequence range: 1-254

These are local-development counts. Recount every target environment immediately before and after migration.

## Runtime cutover

- Project CRUD, ownership filtering, approval, lock, unlock, archive, and protected deletion are server-enforced.
- Saved transcript list/detail/delete use canonical models.
- HTTP ingestion calls `TranscriptService` directly and returns sanitized errors.
- `fetch_transcript` resolves an explicit existing project or an explicit owner for a new project, then calls `TranscriptService`.
- Canonical models and durable import conflicts are registered in admin. Staff can reassign the imported project's owner through the project admin after verifying the intended owner.
- Historical scraper and project URL names remain available as compatibility redirects/aliases where practical.

## Deployment sequence

1. Back up the target database and verify restore before applying migrations. This local implementation did not delete legacy tables, but a target-environment backup remains mandatory.
2. Expand: deploy/apply `0004` while the legacy runtime still operates.
3. Migrate: run `0005` during a controlled write window; reconcile legacy IDs, row counts, chunks, warnings, and errors.
4. Cut over: deploy this runtime and verify that no application path writes legacy models.
5. Operate through a compatibility window while monitoring reconciliation.
6. Contract only in a separately reviewed release. Legacy tables, provenance, conflicts, and the reserved account must not be removed until backup, production-like rehearsal, target reconciliation, and the compatibility window are complete.

Before cutover, tested migration reversal is available. After cutover permits canonical writes, roll forward or restore a verified backup; do not reverse `0004`, because doing so would drop new canonical rows.

## Verification

Completed locally:

- `python manage.py makemigrations --check --dry-run`
- `python manage.py migrate --plan`
- `python manage.py migrate`
- `python manage.py check`
- `python manage.py test`
- Clean virtual-environment installation from `requirements.txt`

The suite covers models, service behavior, uniqueness races, migration forward/reverse behavior, idempotency, conflicts, ownership, views, workflow, command delegation, compatibility routing, and an end-to-end two-source ingestion scenario. External YouTube calls are stubbed at client boundaries.

## Remaining risks and deferred work

- Legacy retirement is intentionally deferred; no contract migration was created.
- The inactive reserved import account is intentionally retained on migration reversal and requires reviewed operational cleanup later.
- SQLite is the verified backend. Rehearse migrations and concurrency behavior on the actual production backend before production cutover if it differs.
- Live YouTube availability, rate limits, and network behavior are not exercised by automated tests.
- The repository began with extensive unrelated working-tree edits/deletions; they were not reset or restored.
- Existing development security settings and unrelated UI/dependency upgrades remain outside this consolidation.
