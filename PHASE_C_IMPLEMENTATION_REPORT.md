# Phase C Implementation Report

Date: 2026-08-31

## Result

Phase C's production artifact and job foundation is implemented in the `production` Django app. The schema, storage boundaries, validation adapter, lifecycle services, dependency invalidation, retention command, admin integration, and deterministic tests are complete.

Gate C passes. FFmpeg/FFprobe 9.0.1 was installed through Windows Package Manager using the hash-verified `Gyan.FFmpeg` package, and the live production health check now succeeds.

## Schema

### MediaAsset

- Project-scoped, generated storage keys that never use an uploaded basename.
- Stable lineage ID plus integer version.
- One approved version per lineage, with older approved files retained as superseded history.
- Draft, validated, approved, stale, superseded, and failed states.
- SHA-256, byte size, duration, dimensions, frame rate, stream presence, detected MIME/container, and codec metadata.
- Origin, rights basis/notes, consent metadata, configuration snapshot, creator, timestamps, and sanitized failure fields.

### PipelineJob

- Project, job type, status, progress, requester, bounded attempts, snapshots, fingerprints, timestamps, and sanitized errors.
- A deterministic idempotency key derived from job type, input fingerprint, and configuration.
- A conditional database constraint preventing duplicate active jobs for the same request.
- Transactional queue, start, monotonic progress, success, failure, bounded retry, and cancellation transitions.

### ArtifactDependency

- Explicit generic upstream and downstream object references.
- Both artifact versions plus the recorded upstream fingerprint.
- Relation type and uniqueness constraint.
- Explicit service-driven invalidation; there are no hidden model signals.

## Storage and media validation

- Local development uses Django's configured `FileSystemStorage` and `MEDIA_ROOT`.
- Deployments can replace the default backend through Django's Storage API without changing domain models.
- Uploads are spooled to a generated temporary file, size-limited while streaming, and SHA-256 hashed.
- Extensions are allow-listed and compared with the container detected by FFprobe.
- FFprobe is invoked using a fixed executable, argument array, `shell=False`, a timeout, local paths only, and sanitized error categories.
- Invalid uploads leave no database row or stored media file.
- Project ownership and approved/unlocked lifecycle rules are rechecked inside write transactions.

## Operations

- `python manage.py check_production_health` reports the storage backend and validates FFprobe availability.
- `python manage.py cleanup_media_assets --older-than-days N` is dry-run by default.
- Cleanup requires `--execute`, only considers old failed/superseded assets, and skips approved/current or dependency-referenced assets.
- No background worker is introduced yet because no capture, TTS, or render job is exposed to normal UI use. The job contract is ready for a runner when measured Phase D work requires one.

## Verification

- `python manage.py makemigrations --check`: passed.
- `python manage.py migrate`: production migrations applied.
- `python manage.py migrate --plan`: no pending operations after application.
- `python manage.py check`: passed.
- `python manage.py test production`: 28 tests passed.
- `python manage.py test`: 72 tests passed after all Phase C migrations and approval-lineage hardening.
- `ffmpeg -version`: passed with FFmpeg 9.0.1 full build.
- `ffprobe -version`: passed with FFprobe 9.0.1 full build.
- `python manage.py check_production_health`: passed against the configured `FileSystemStorage` and live FFprobe executable.
- `python manage.py test production`: 28 tests passed again after the live tool installation.

## Next permitted action

Gate C is unblocked. A terminal or IDE process that was already open during installation may need to be restarted so it inherits the updated user `PATH`. Confirm with:

```powershell
ffmpeg -version
ffprobe -version
python manage.py check_production_health
```

Phase D may now begin with an authorized local source file and deterministic clip trimming. Automated download or browser capture is not authorized by Phase C.
