# Temporal place context tool

**Status:** implementation plan  
**Date:** 2026-09-10  
**Depends on:** temporal hierarchy + historical CHD place identities (#55, #56)

## Intent

Expose Soundings' existing validity-aware containment service as a first-class tool so question-shaped clients can ask which places contained a geography now or at a historical date.

This is an exposure slice, not a new geography-model slice. The underlying semantics remain those already implemented by `GeographyService.find_containing_places_context()`.

## Tool contract

Add `get_containing_places` with:

- `place_id` — canonical Soundings place ID;
- `boundary_mode` — `current_boundary` (default) or `historical`;
- `as_of` — optional date; required by the service for historical mode.

Return:

- the requested `place_id`;
- containing places (`id`, `name`, `type`, `code`);
- `boundary_mode`;
- the actual `boundary_date` used;
- `as_of` when supplied;
- `partial` and `caveats` from the geography service.

Do not silently fall back from historical to current boundaries. Historical mode without `as_of` should fail validation before reaching the service.

## Surfaces

Use the same implementation across:

1. in-process Ask dispatcher;
2. HTTP `/v1/tools/get_containing_places` and `/v1/tools` catalogue;
3. MCP `get_containing_places`.

The Ask tool description should make the intended question shape explicit: use it for questions such as “which local authority contained this area in 2015?” or “what contains this place now?”.

## Historical resolution flow

A historical place question can be answered in two explicit steps:

1. `find_place(query=..., as_of=YYYY-MM-DD)` resolves the dated place identity;
2. `get_containing_places(place_id=..., boundary_mode="historical", as_of=YYYY-MM-DD)` traverses dated CHD edges.

This keeps name resolution and containment separate and inspectable.

## Scope boundaries

Deliberately defer:

- historical postcode membership;
- historical geometries/boundary rendering;
- current-boundary remapping of historical observations;
- adding new GSS entity-label taxonomy;
- descendants/sub-area history;
- automatic interpretation of natural-language dates inside the tool itself.

## Tests

Add coverage for:

- input defaults and validation;
- historical mode requiring `as_of`;
- output round-trip;
- tool spec;
- current and historical service routing;
- propagation of partial/caveats;
- Ask dispatcher registration and dispatch;
- HTTP tool catalogue + route;
- MCP registration smoke coverage where existing patterns make that cheap.

## Acceptance criteria

- A caller can retrieve current containing places through one tool call.
- A caller can retrieve historical containing places for a resolved historical place and date.
- Historical mode never substitutes undated/current hierarchy evidence.
- Partial historical evidence remains visible to the caller.
- Ask, HTTP and MCP expose the same semantics.
- Full server, lint/type and UI CI remain green.
