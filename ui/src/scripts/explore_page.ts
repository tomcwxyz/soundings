// Client logic for the map explorer (/explore). Picks an indicator + geography
// level, fetches the national choropleth, and (re)renders it. Rebuild-on-change
// is fine for the skeleton; an in-place stateful map is a later refinement.

interface ExploreIndicator {
  key: string;
  label: string;
  available_at: string[];
}

const LEVEL_LABELS: Record<string, string> = {
  lsoa21: "Neighbourhood (LSOA)",
  ltla24: "Local authority",
  utla24: "Upper-tier authority",
  region: "Region",
};
// Preferred order for the level dropdown (coarse → fine reads best as options).
const LEVEL_ORDER = ["ltla24", "utla24", "region", "lsoa21"];

let maplibreCssLoaded = false;
function ensureMaplibreCss(): void {
  if (maplibreCssLoaded) return;
  maplibreCssLoaded = true;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css";
  document.head.appendChild(link);
}

function init(): void {
  const surface = document.getElementById("explore-map");
  const indicatorSel = document.getElementById("explore-indicator") as HTMLSelectElement | null;
  const status = document.getElementById("explore-status");
  const dataEl = document.getElementById("explore-indicators-data");
  if (!surface || !indicatorSel || !dataEl) return;

  const apiBase = surface.dataset.apiBase || "";
  const tilesUrl = surface.dataset.mapTiles || undefined;
  const panel = document.getElementById("explore-panel");
  const overlayInputs = Array.from(
    document.querySelectorAll<HTMLInputElement>("#explore-overlays input[type=checkbox]"),
  );
  const overlayHint = document.getElementById("explore-overlay-hint");
  const indicators = JSON.parse(dataEl.textContent || "[]") as ExploreIndicator[];
  const byKey = new Map(indicators.map((i) => [i.key, i]));

  // Contextual indicators shown in the side panel alongside the active one.
  const HEADLINE = [
    "population.total",
    "deprivation.imd.score",
    "economy.active_companies_count",
    "environment.greenspace.area_per_capita",
  ];

  function esc(s: string): string {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function labelFor(key: string): string {
    const known = byKey.get(key)?.label;
    if (known) return known;
    const [head, ...rest] = key.split(".");
    const h = head ? head[0]!.toUpperCase() + head.slice(1) : key;
    const tail = rest.join(" · ").replaceAll("_", " ");
    return tail ? `${h}: ${tail}` : h;
  }

  interface IndicatorResult {
    indicator: string;
    value: number | null;
    unit?: string | null;
  }

  let selectedPlaceId: string | null = null;

  async function showPanel(sel: { placeId?: string; name: string }): Promise<void> {
    if (!panel) return;
    selectedPlaceId = sel.placeId ?? null;
    panel.innerHTML = `<h2>${esc(sel.name)}</h2><p class="text-muted text-small">Loading…</p>`;
    const keys = Array.from(new Set([indicatorSel!.value, ...HEADLINE]));
    let results: IndicatorResult[] = [];
    if (sel.placeId) {
      try {
        const res = await fetch(`${apiBase}/v1/tools/get_indicators`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ place_id: sel.placeId, indicators: keys }),
        });
        if (res.ok) results = ((await res.json()).results ?? []) as IndicatorResult[];
      } catch {
        /* leave results empty; panel shows the no-data note */
      }
    }
    const rows = results
      .filter((r) => typeof r.value === "number")
      .map(
        (r) =>
          `<div class="panel-row"><span class="panel-label">${esc(labelFor(r.indicator))}</span>` +
          `<span>${(r.value as number).toLocaleString("en-GB")}${r.unit ? " " + esc(r.unit) : ""}</span></div>`,
      )
      .join("");
    const link = sel.placeId
      ? `<a class="panel-link" href="/place/${encodeURIComponent(sel.placeId)}">View full profile →</a>`
      : "";
    const askLink = sel.placeId
      ? `<a class="panel-link" href="/ask?q=${encodeURIComponent(`What should we pay attention to in ${sel.name}?`)}&place_id=${encodeURIComponent(sel.placeId)}">Ask about this place →</a>`
      : "";
    panel.innerHTML =
      `<h2>${esc(sel.name)}</h2>` +
      (rows || `<p class="text-muted text-small">No indicator data for this area.</p>`) +
      link +
      askLink;

    // Offer a drill-down into this area's neighbourhoods when it's an authority
    // and the active indicator has LSOA-level data.
    const activeKey = indicatorSel!.value;
    const canDrill =
      !drillPlaceId &&
      !!sel.placeId &&
      (sel.placeId.startsWith("ltla24:") || sel.placeId.startsWith("utla24:")) &&
      (byKey.get(activeKey)?.available_at.includes("lsoa21") ?? false);
    if (canDrill && sel.placeId) {
      const placeId = sel.placeId;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "panel-drill";
      btn.textContent = "Explore its neighbourhoods →";
      btn.addEventListener("click", () => {
        drillPlaceId = placeId;
        drillName = sel.name;
        selectedPlaceId = null;
        void render();
      });
      panel.appendChild(btn);
    }
  }

  const backBtn = document.getElementById("explore-back") as HTMLButtonElement | null;
  let drillPlaceId: string | null = null;
  let drillName: string | null = null;
  let cleanup: (() => void) | null = null;

  function selectedOverlayKeys(): string[] {
    return overlayInputs.filter((input) => input.checked).map((input) => input.value);
  }

  function syncOverlayControls(): void {
    const enabled = Boolean(drillPlaceId);
    for (const input of overlayInputs) input.disabled = !enabled;
    if (overlayHint) {
      overlayHint.textContent = enabled
        ? "Toggle local provision on top of the neighbourhood map."
        : "Drill into an authority to compare need and provision.";
    }
  }

  // Each indicator shows at its default (coarsest available) level — a sensible
  // national overview — with drill-down for neighbourhoods. No manual level
  // picker (it confused more than it helped).
  function defaultLevel(key: string): string {
    const avail = byKey.get(key)?.available_at ?? [];
    return LEVEL_ORDER.find((l) => avail.includes(l)) ?? avail[0] ?? "ltla24";
  }

  async function render(): Promise<void> {
    const key = indicatorSel!.value;
    if (!key) return;

    cleanup?.();
    cleanup = null;
    if (backBtn) backBtn.hidden = !drillPlaceId;
    syncOverlayControls();
    if (status) status.textContent = "Loading…";

    // Drill mode: the selected area's LSOAs. National mode: all areas of the
    // chosen level.
    let url: string;
    let contextLabel: string;
    if (drillPlaceId) {
      url =
        `${apiBase}/v1/place/${encodeURIComponent(drillPlaceId)}/children/geometry` +
        `?indicator=${encodeURIComponent(key)}&child_type=lsoa21`;
      contextLabel = `neighbourhoods in ${drillName ?? "this area"}`;
    } else {
      const level = defaultLevel(key);
      const large = level === "lsoa21" ? "&large=true" : "";
      url =
        `${apiBase}/v1/geographies/${encodeURIComponent(level)}/geometry` +
        `?indicator=${encodeURIComponent(key)}${large}`;
      contextLabel = `${LEVEL_LABELS[level] ?? level} areas`;
    }

    let fc: GeoJSON.FeatureCollection;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      fc = (await res.json()) as GeoJSON.FeatureCollection;
    } catch (err) {
      if (status) {
        status.textContent =
          "Could not load map: " + (err instanceof Error ? err.message : String(err));
      }
      return;
    }

    let points: GeoJSON.FeatureCollection | undefined;
    let overlayError: string | null = null;
    const overlayKeys = selectedOverlayKeys();
    if (drillPlaceId && overlayKeys.length > 0) {
      try {
        const pointsRes = await fetch(
          `${apiBase}/v1/place/${encodeURIComponent(drillPlaceId)}/amenities/geometry?indicators=${encodeURIComponent(overlayKeys.join(","))}`,
        );
        if (!pointsRes.ok) throw new Error(`${pointsRes.status} ${pointsRes.statusText}`);
        points = (await pointsRes.json()) as GeoJSON.FeatureCollection;
      } catch (err) {
        overlayError = err instanceof Error ? err.message : String(err);
      }
    }

    ensureMaplibreCss();
    surface!.innerHTML = "";
    const { renderChoroplethMap } = await import("../lib/map-renderer");
    cleanup = renderChoroplethMap(surface!, fc, "value", {
      label: byKey.get(key)?.label ?? key,
      indicatorKey: key,
      selectedPlaceId: selectedPlaceId ?? undefined,
      tilesUrl,
      points,
      onSelectArea: (sel) => void showPanel(sel),
    });
    const n = (fc.features ?? []).filter(
      (f) => typeof f.properties?.value === "number",
    ).length;
    if (status) {
      const pointCount = points?.features?.length ?? 0;
      const overlayStatus =
        overlayKeys.length > 0
          ? overlayError
            ? ` · provision layer unavailable (${overlayError})`
            : ` · ${pointCount} provision points`
          : "";
      status.textContent =
        `${n} ${contextLabel} with data${overlayStatus} · click an area for details`;
    }
  }

  indicatorSel.addEventListener("change", () => {
    // New indicator → its default national view (out of any drill-down).
    drillPlaceId = null;
    drillName = null;
    selectedPlaceId = null;
    void render();
  });
  backBtn?.addEventListener("click", () => {
    drillPlaceId = null;
    drillName = null;
    selectedPlaceId = null;
    void render();
  });

  for (const input of overlayInputs) {
    input.addEventListener("change", () => void render());
  }

  void render();
}

init();
