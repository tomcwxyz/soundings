# ADR-0004: Corpus publication scope in v1 (local artefacts)

**Status:** Accepted, amended 2026-08-29  
**Date:** 2026-05-11  
**Context:** Phase 2 — Block E publication tasks
(`docs/plans/2026-05-11-soundings-v1-phase-2-plan.md`).

## Decision

Corpus snapshots remain **local-first artefacts**, but publication is now
automatic on the operated Docker instance.

The publication job writes to the durable `corpus_data` Docker volume shared
by the `loader` and `server` services:

- `corpus-YYYY-MM.csv.gz` (flattened-wide);
- `corpus-YYYY-MM.jsonl.gz` (full nested);
- `manifest.json` (SHA-256s + catalogue version + sanitisation rules version
  + generator git sha when available).

The files are cumulative snapshots: `corpus-2026-07.*` contains every cleared,
consented corpus record with a timestamp before 1 August 2026. This preserves
the existing publication contract.

## Automatic schedule

The existing loader daemon owns the publication schedule:

- **04:30 UTC on the first day of each month**;
- target period is the previous calendar month;
- on every loader startup, Soundings checks the current manifest and catches up
  the previous month if that publication was missed;
- if the manifest is already current (or manually ahead), publication is
  skipped;
- publication failure does not stop source loaders, and the existing alert path
  is used for the failure.

This means a host outage on the first of the month does not permanently miss a
snapshot: the next loader start catches it up.

## Persistence and serving

Docker Compose mounts the same named `corpus_data` volume at `/app/corpus`
for both the API and loader containers. The server image still contains the
checked-in seed corpus so a newly created Docker volume starts with the latest
repository snapshot, while subsequent generated snapshots survive rebuilds and
container replacement.

The public UI/API serves the latest manifest and its files through
`/v1/corpus/*`.

## Manual publication

`make publish-corpus PERIOD=YYYY-MM` remains available for recovery,
verification and deliberate re-publication. Manual CLI runs can still create a
local git tag; the automatic container job deliberately does not because the
runtime image is not a Git checkout.

## Hosted replication

The original design described pushing snapshots to Backblaze B2. That remains a
separate optional replication step, gated on:

1. a B2 bucket;
2. encrypted `B2_KEY_ID` / `B2_APPLICATION_KEY` material in
   `soundings-ops`;
3. a retention/restore decision for the remote archive.

Local automatic publication is the source of truth until that replication layer
is added.
