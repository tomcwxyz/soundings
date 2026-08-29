# Soundings

> *Taking the measure of local need.*

Soundings is an open insight commons for understanding what is happening in places across the UK. It brings UK open data behind a common geography and indicator layer, exposes question-shaped tools over HTTP and MCP, and provides a natural-language interface that answers from those tools rather than from general model knowledge.

Consented questions can also contribute to a public corpus, so the questions people ask about places become part of the shared learning infrastructure.

## Status

**Phase 6 — consolidation after the first depth + breadth releases.**

Phases 0–5 are complete. Phase 6a shipped the natural-language Ask interface, neighbourhood-level analysis, Give Food integration and richer civil-society context. Phase 6b has added further data breadth, including Companies House, green-space, air-quality and sport/activity data.

On 29 August 2026 the repository went through a consolidation pass to restore a green CI baseline, reconcile stale contracts/tests and make the current state explicit. See [STATE.md](./STATE.md) for the live technical position and [HANDOFF.md](./HANDOFF.md) for the current development handoff.

## What is here

- **Geography spine** — canonical UK place IDs, hierarchies, geometries and postcode resolution.
- **Indicator catalogue** — source and indicator metadata is the contract between loaders, tools and UI.
- **Data adapters** — loader and passthrough integrations for official/open sources.
- **Postgres + PostGIS** — normalised place, indicator, organisation, cache and capture data.
- **Question-shaped tools** — place lookup, profiles, indicators, comparisons, trends, neighbourhoods, organisations and civil-society profiles.
- **HTTP + MCP** — the same underlying tools are available to the web application and compatible agent clients.
- **Ask** — Claude tool-use orchestration over Soundings data with typed answer blocks, maps/charts and multi-turn follow-ups.
- **Astro UI** — place pages, comparisons, maps, charts and the Ask interface.
- **Question corpus** — consent-aware capture, sanitisation and publication tooling.

## Repository structure

```
catalogue/   source + indicator definitions
corpus/      published corpus material
docs/        specs, ADRs, plans and runbooks
infra/       generic Docker/Caddy/database infrastructure
server/      FastAPI, MCP, adapters, orchestration and tests
ui/          Astro web application
```

## Quick start

For normal local development, start from the public environment template:

```bash
cp .env.example .env
# review .env and add any API keys needed for the features you are testing

make up
make migrate
make seed-light
```

The web stack is containerised through `infra/docker-compose.yml`. Ask requires an Anthropic API key; most core data/tool tests do not.

Useful checks:

```bash
make lint
make type
make test
cd ui && npm test
cd ui && npm run typecheck
```

Live upstream API tests are intentionally separate from the normal CI suite and run through the nightly workflow.

## Public code vs private operations

This repository contains the reusable application and generic deployment infrastructure and is intentionally public.

`tomcwxyz/soundings-ops` is the private companion for **instance-specific operations**: encrypted environment material, host-specific configuration, backup/restore settings and other production-only details. Secrets should never be committed here.

## Licensing

See [LICENSE.md](./LICENSE.md).

- Server code: AGPL-3.0
- Schema: CC0
- Specs/documentation: CC BY 4.0

Maintained by [The Good Ship](https://good-ship.co.uk).
