# 360Giving query/index layer

Date: 2026-09-07

## Decision

Keep grants intelligence inside Soundings while the contracts and access
patterns mature. Build the core without HTTP/MCP dependencies so it can be
extracted later into a dedicated grants/360Giving service without rewriting the
query layer.

## Why now

Soundings already has an official 360Giving API client and a passthrough adapter,
but the API is organisation-centric. Place queries currently fan out across many
Charity Commission organisations, which is slow enough that per-organisation
grant enrichment was disabled in `find_organisations_in_place`.

The existing `data.grant_record` table was designed for this eventuality but has
not previously been populated. It becomes the canonical local grants index.

## Access pattern

Use two complementary paths:

1. **Targeted hydration** — the official 360Giving API for exact organisation or
   funder lookups. These calls write through into `data.grant_record`.
2. **Corpus indexing** — GrantNav full-dataset CSV/JSON or 360Giving Datastore
   access for broad search and analysis. Do not fan out the official API to try
   to reconstruct the corpus.

The first implementation includes a safe local GrantNav CSV importer. Automated
retrieval is deliberately separate so we can use a documented/stable bulk route
rather than scrape the GrantNav UI.

## Increment 1 — query/index foundation

- [x] Extend `data.grant_record` with searchable human-readable grant fields and
      preserved raw source payload.
- [x] Add PostgreSQL full-text search plus date, funder, recipient and place
      indexes.
- [x] Add transport-independent `soundings.grants.GrantStore`.
- [x] Add `search_grants` tool: topic, funder, recipient, place, date, amount,
      pagination.
- [x] Add `get_funder_profile` tool with targeted API hydration for exact Org
      IDs.
- [x] Expose both tools over HTTP and MCP.
- [x] Correct the API client to the documented 2 requests/second limit, use
      1,000-record pages, set the JSON Accept header and handle 429 responses.
- [x] Add safe GrantNav CSV import, with replace-after-success semantics for a
      declared full-corpus import.
- [x] Make index coverage explicit (`partial-write-through` vs
      `full-grantnav-export`) so an agent cannot mistake a partial index for the
      full 360Giving corpus.
- [ ] Wire grant tools into the in-process `/ask` dispatcher after CI confirms
      the base contracts.

## Increment 2 — complete corpus and geography

- [ ] Choose the production bulk route:
  - preferred: 360Giving Datastore read-only access if credentials are available;
  - otherwise: documented GrantNav full-dataset export with a stable retrieval
    contract.
- [ ] Schedule daily refresh after the bulk route is proven.
- [ ] Resolve Grant Location / Beneficiary Location geographic codes to the
      Soundings geography spine during import.
- [ ] Record publisher/dataset provenance sufficiently to support data
      corrections and removals.
- [ ] Rework place-level 360Giving indicators to query `data.grant_record`
      instead of live fan-out when the index is complete.
- [ ] Re-enable fast per-organisation recent-grant enrichment in
      `find_organisations_in_place` from the local index.
- [ ] Make `funded_only` use the local index once coverage is complete.

## Increment 3 — grants intelligence

Only add analytical tools once the corpus is complete enough that the answers
are meaningful:

- recipient profile / funding history;
- funders active in a place;
- funder comparison;
- similar recipients;
- relevant funders based on observed funding patterns;
- funding landscape by topic + geography + period;
- concentration, repeat-funding and trajectory measures.

Keep these evidence-first: return supporting grants and deterministic
aggregations. Do not embed an LLM inside the grants layer.

## Extraction trigger

Do not extract yet. Consider a standalone grants MCP/service when all of these
are true:

1. the bulk ingest path is stable;
2. the core query contracts have survived real Soundings use;
3. at least one consumer other than Soundings needs the same capabilities; and
4. Soundings-specific geography/organisation joins have clear adapter
   boundaries.

At that point the intended extraction boundary is `soundings/grants/` plus the
360Giving client/import components; Soundings retains thin adapters/tools around
that service.
