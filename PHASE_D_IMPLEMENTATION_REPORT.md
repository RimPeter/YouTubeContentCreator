# Phase D Implementation Report

Date: 2026-08-31

## Result

Phase D is implemented and Gate D passes. One selected transcript segment can now be paired with an explicitly authorized local video, trimmed with FFmpeg, validated with FFprobe, previewed through an authenticated endpoint, and approved as a versioned source clip.

No YouTube download, browser automation, screen recording, or remote media acquisition was added.

## SourceClip contract

`SourceClip` records:

- project and selected `SegmentSelection`
- validated source-upload `MediaAsset`
- generated source-clip `MediaAsset`
- the existing `PipelineJob` used for the trim
- version, status, and deterministic input fingerprint
- requested transcript boundaries, before/after padding, and resolved source boundaries
- expected and measured output duration
- structured validation results
- creator, approver, lifecycle timestamps, and sanitized failure fields

Database constraints enforce positive durations, ordered boundaries, non-negative padding, unique versions per selection, and at most one approved clip per selection.

## Authorized local-media workflow

- The owner uploads a local video and must choose a rights basis and explicitly confirm authorization.
- Upload validation from Phase C verifies size, extension/container agreement, streams, checksum, duration, dimensions, frame rate, and codecs.
- A source clip can use only a current validated/approved source-upload asset with a video stream, measured duration, non-unknown rights basis, and rights confirmation.
- Selected boundaries use reviewed timestamps when present and otherwise use the analysis-segment boundaries.
- Configurable padding is clamped to the uploaded source duration.
- Source timestamps outside the uploaded media duration are rejected.

## FFmpeg processing

- `FFMPEG_EXECUTABLE` and `FFMPEG_TIMEOUT_SECONDS` are configurable.
- FFmpeg receives a fixed argument array with `shell=False`, `-nostdin`, bounded execution time, and controlled local input/output paths.
- The clip is re-encoded with H.264/AAC for frame-accurate trimming and web-compatible preview.
- Output is re-probed and must contain video, remain within duration tolerance, and meet configured resolution/frame-rate limits.
- Raw FFmpeg stderr, file paths, and command details are not persisted or shown to users.

## Jobs, dependencies, and versioning

- Trimming uses the Phase C `PipelineJob` contract with deterministic idempotency, bounded attempts, progress, success, and audited failure.
- Equivalent successful requests reuse the existing clip rather than producing duplicates.
- Regeneration creates a new `SourceClip` version and a new version in the processed-media lineage.
- Approval supersedes—but does not delete—the previously approved clip and file.
- Explicit dependency edges connect the output media to both the selected segment and source upload.
- Reviewed boundary changes immediately mark affected clips and processed media stale.
- Notes and selection ordering do not invalidate media because they do not alter clip content.
- Inputs are checked under a project lock before FFmpeg and checked again before final persistence, preventing concurrent edits from producing falsely current output.

## UI and access control

- Project and analysis pages link to the source-clip dashboard.
- The dashboard supports rights-confirmed uploads, source selection, configurable padding, generation, and clip-version history.
- Clip detail shows validation metadata, checksum, timing, approval action, and an HTML video preview.
- Preview media is served through an authenticated owner/staff endpoint with byte-range support, private/no-store caching, and MIME sniffing disabled.
- Mutations are POST-only and CSRF-protected; cross-user objects return 404.

## Verification

- `python manage.py makemigrations --check`: passed.
- `python manage.py migrate`: `production.0003_sourceclip` applied.
- `python manage.py migrate --plan`: no pending operations.
- `python manage.py check`: passed.
- `python manage.py check_production_health`: passed with FFmpeg/FFprobe 9.0.1.
- Phase D real-tool integration: passed using a generated 160x90 video with audio; no downloaded media was used.
- `python manage.py test`: all 89 tests passed with the real FFmpeg integration enabled.

## Operational boundary

This first vertical slice runs synchronously and is proven with tiny media. Before large uploads or normal multi-user capture/render work, add the background runner required by the plan, including queue health, cancellation propagation, concurrency limits, and transactional enqueue behavior. The current FFmpeg timeout is a safety bound, not evidence that long web requests are production-ready.

## Next permitted work

Gate D permits Phase E: build one evidence-backed, versioned ReactionBlock for an approved source clip. Research findings must retain citation provenance and require human verification; provider-generated URLs or factual claims must never be treated as verified automatically.
