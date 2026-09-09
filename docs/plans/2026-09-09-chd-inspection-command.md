# CHD inspection command

**Status:** implementation slice  
**Date:** 2026-09-09  
**Depends on:** PR #53 CHD archive inspection

## Goal

Make the structure-aware CHD inspector usable against a real ONS release without
requiring a database or mutating Soundings data.

The immediate purpose is to inspect the real Geography Hierarchies table schema
before implementing historical hierarchy ingestion.

## Command

Add a small module runnable as:

```bash
cd server
uv run python -m soundings.adapters.ons_geography.chd_inspect
```

Options:

- `--url URL` — inspect a specific CHD zip; defaults to the current ONS CHD URL.
- `--sample-size N` — number of sample rows per CSV, default 2.

The command downloads the zip, calls `inspect_chd_archive`, and prints a JSON
report containing source URL and the inventory summary.

## Constraints

- Read-only: no database connection or writes.
- Do not interpret hierarchy fields yet.
- Fail non-zero on download/zip/inspection errors.
- Keep samples bounded so diagnostics remain small.
- Reuse the existing CHD URL rather than duplicating a second default endpoint.

## Acceptance criteria

- Current CHD can be inspected with one command.
- An alternate archived CHD URL can be supplied.
- JSON output is machine-readable and contains discovered table names, kinds,
  headers and bounded samples.
- Tests cover rendering and argument validation without hitting the live ONS
  endpoint.
- Normal Ruff, strict mypy and test gates remain green.
