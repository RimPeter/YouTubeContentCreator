# Phase E implementation report

Date: 2026-09-01

## Outcome

Phase E is implemented as a new `editorial` Django app. It supports a complete local workflow from an approved `SourceClip` to immutable reviewed research and a versioned, traceable reaction draft.

The implementation gate is complete. The product Gate E remains an explicit human action: a project owner must review a real script, confirm its evidence and substantial originality, and approve it in the UI. The software does not automate that editorial or legal judgment.

## Delivered

- Versioned `ResearchPackage`, `EvidenceSource`, `ReactionBlock`, and `ReactionClaim` models with additive migration `editorial.0001_initial`.
- Draft/ready/stale/superseded research lifecycle and generating/draft/approved/failed/stale/superseded reaction lifecycle.
- Manual source entry with public HTTP(S) URL validation, concise field limits, and explicit human verify/reject actions.
- Ready research is immutable; changes require a new version.
- Structured provider boundary with exact schema validation, bounded retries, untrusted-content instructions, citation ID validation, and no provider-created URL fields.
- Deterministic transcript-only fallback when no provider is configured or provider output fails.
- Reproducible combined scripts assembled from stored subsections.
- Factual claims require a selected transcript chunk or a human-verified `EvidenceSource`; opinion and inference claims are explicitly typed.
- Approval requires evidence-review and originality attestations, current inputs, valid claims, and a reproducible combined script.
- Artifact dependency edges from the approved source clip and ready research package to the approved reaction.
- Editorial artifacts become stale when their source clip becomes stale or is superseded; a new ready research version invalidates reactions based on the previous version.
- Owner/staff-scoped authenticated UI, POST-only mutations, CSRF protection, admin integration, and a link from approved source clips.

## Verification

- `python manage.py makemigrations --check` — pass; no changes detected.
- `python manage.py migrate` — pass; `editorial.0001_initial` applied.
- `python manage.py check` — pass; no issues.
- `python manage.py test editorial` — 17 tests pass.
- `python manage.py test` — pass; 106 tests run, with one environment-dependent test skipped.

Coverage includes migration forward/reverse behavior, authorization, CSRF and POST enforcement, URL safety, human evidence verification, immutable versions, deterministic output, valid and malformed provider schemas, unsupported factual claims, prompt-injection content, bounded provider failure, persistence rollback, approval attestations, dependency creation, and stale invalidation.

## Deliberate limits

- No live AI or web-research provider is configured. The provider interface is injectable and automated tests make no network calls.
- The default UI generation path uses the deterministic fallback; it is a starting draft, not a finished editorial product.
- URL validation prevents unsafe/local URL acceptance but does not fetch a page or guarantee accessibility. Human verification is required.
- No legal conclusion, copyright clearance, or originality judgment is automated.

## Next permitted phase

After a user completes Gate E with one real approved reaction, Phase F may implement narration, licensed/user-supplied visuals, and one 60–120 second mini-video vertical slice.
