// Client logic for the map explorer (/explore).
//
// One stateful InteractiveMap instance survives indicator, drill-down and
// provision-layer changes so camera position, selection and map interaction do
// not reset on every control change.

interface ExploreIndicator {
  key: string;
  label: string;
  available_at: string[];
}

interface IndicatorResult {
  indicator: string;
  value: number | null;
  unit?: string | null;
}

interface ContainingPlace {
  id: string;
  name: string;
  type: string;
}

interface ContainingPlacesResponse {
  places?: ContainingPlace[];
}

type InteractiveMapInstance = import("../lib/interactive-map").InteractiveMap;

const LEVEL_LABELS: Record<string, string> = {
  lsoa21: "Neighbourhood (LSOA)",
  ltla24: "Local authority",
  utla24: "Upper-tier authority",
  region: "Region",
};
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
  const indicatorSel = document.getElementById(
    "explore-indicator",
  ) as HTMLSelectElement | null;
  const status = document.getElementById("explore-status");
  const dataEl = document.getElementById("explore-indicators-data");
  if (!surface || !indicatorSel || !dataEl) return;

  const apiBase = surface.dataset.apiBase || "";
  const tilesUrl = surface.dataset.mapTiles || undefined;
  const panel = document.getElementById("explore-panel");
  const backBtn = document.getElementById(
    "explore-back",
  ) as HTMLButtonElement | null;
  const overlayInputs = Array.from(
    document.querySelectorAll<HTMLInputElement>(
      "#explore-overlays input[type=checkbox]",
    ),
  );
  const overlayHint = document.getElementById("explore-overlay-hint");
  const indicators = JSON.parse(
    dataEl.textContent || "[]",
  ) as ExploreIndicator[];
  const byKey = new Map(indicators.map((indicator) => [indicator.key, indicator]));

  const HEADLINE = [
    "population.total",
    "deprivation.imd.score",
    "economy.active_companies_count",
    "environment.greenspace.area_per_capita",
  ];

  let drillPlaceId: string | null = null;
  let drillName: string | null = null;
  let selectedPlaceId: string | null = null;
  let currentContextKey: string | null = null;
  let mapPromise: Promise<InteractiveMapInstance> | null = null;
  const containingAuthorityCache = new Map<string, ContainingPlace | null>();

  function esc(value: string): string {
    return value
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

  function selectedOverlayKeys(): string[] {
    return overlayInputs
      .filter((input) => input.checked)
      .map((input) => input.value);
  }

  function syncOverlayControls(): void {
    const enabled = Boolean(drillPlaceId);
    for (const input of overlayInputs) input.disabled = !enabled;
    if (overlayHint) {
      overlayHint.textContent = enabled
        ? "Toggle local provision on top of the neighbourhood map."
        : "Focus on an authority to compare need and provision.";
    }
  }

  function resetPanel(): void {
    if (!panel) return;
    panel.innerHTML =
      '<p class="explore-panel-empty text-muted text-small">Click an area on the map to see its details.</p>';
  }

  function defaultLevel(key: string): string {
    const available = byKey.get(key)?.available_at ?? [];
    return (
      LEVEL_ORDER.find((level) => available.includes(level)) ??
      available[0] ??
      "ltla24"
    );
  }

  async function getMap(): Promise<InteractiveMapInstance> {
    if (!mapPromise) {
      ensureMaplibreCss();
      mapPromise = import("../lib/interactive-map").then(
        ({ InteractiveMap }) =>
          new InteractiveMap(surface, {
            tilesUrl,
            onSelectArea: (selection) => void showPanel(selection),
          }),
      );
    }
    return mapPromise;
  }

  async function fetchIndicatorRows(
    placeId: string,
    keys: string[],
  ): Promise<IndicatorResult[]> {
    try {
      const res = await fetch(`${apiBase}/v1/tools/get_indicators`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ place_id: placeId, indicators: keys }),
      });
      if (!res.ok) return [];
      return ((await res.json()).results ?? []) as IndicatorResult[];
    } catch {
      return [];
    }
  }

  async function containingAuthority(
    placeId: string,
  ): Promise<ContainingPlace | null> {
    if (!placeId.startsWith("lsoa21:")) return null;
    if (containingAuthorityCache.has(placeId)) {
      return containingAuthorityCache.get(placeId) ?? null;
    }

    try {
      const res = await fetch(`${apiBase}/v1/tools/get_containing_places`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ place_id: placeId }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const payload = (await res.json()) as ContainingPlacesResponse;
      const authority =
        payload.places?.find((place) => place.type === "ltla24") ??
        payload.places?.find((place) => place.type === "utla24") ??
        null;
      containingAuthorityCache.set(placeId, authority);
      return authority;
    } catch {
      containingAuthorityCache.set(placeId, null);
      return null;
    }
  }

  async function showPanel(selection: {
    placeId?: string;
    name: string;
  }): Promise<void> {
    if (!panel) return;

    selectedPlaceId = selection.placeId ?? null;
    panel.innerHTML =
      `<h2>${esc(selection.name)}</h2><p class="text-muted text-small">Loading…</p>`;

    const keys = Array.from(
      new Set([indicatorSel!.value, ...HEADLINE]),
    );
    const [results, parentAuthority] = await Promise.all([
      selection.placeId
        ? fetchIndicatorRows(selection.placeId, keys)
        : Promise.resolve([]),
      selection.placeId
        ? containingAuthority(selection.placeId)
        : Promise.resolve(null),
    ]);

    const rows = results
      .filter((result) => typeof result.value === "number")
      .map(
        (result) =>
          `<div class="panel-row"><span class="panel-label">${esc(labelFor(result.indicator))}</span>` +
          `<span>${(result.value as number).toLocaleString("en-GB")}${result.unit ? " " + esc(result.unit) : ""}</span></div>`,
      )
      .join("");

    const profileLink = selection.placeId
      ? `<a class="panel-link" href="/place/${encodeURIComponent(selection.placeId)}">View full profile →</a>`
      : "";
    const askLink = selection.placeId
      ? `<a class="panel-link" href="/ask?q=${encodeURIComponent(`What should we pay attention to in ${selection.name}?`)}&place_id=${encodeURIComponent(selection.placeId)}">Ask about this place →</a>`
      : "";

    panel.innerHTML =
      `<h2>${esc(selection.name)}</h2>` +
      (rows ||
        '<p class="text-muted text-small">No indicator data for this area.</p>') +
      profileLink +
      askLink;

    const activeKey = indicatorSel!.value;
    const supportsNeighbourhoods =
      byKey.get(activeKey)?.available_at.includes("lsoa21") ?? false;

    const selectedAuthority =
      selection.placeId &&
      (selection.placeId.startsWith("ltla24:") ||
        selection.placeId.startsWith("utla24:"))
        ? { id: selection.placeId, name: selection.name }
        : null;
    const focusAuthority = selectedAuthority ?? parentAuthority;

    if (!drillPlaceId && focusAuthority && supportsNeighbourhoods) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "panel-drill";
      button.textContent = `Focus on neighbourhoods in ${focusAuthority.name} →`;
      button.addEventListener("click", () => {
        drillPlaceId = focusAuthority.id;
        drillName = focusAuthority.name;

        // If the user arrived here by clicking an LSOA on the national map,
        // keep that neighbourhood selected after focusing into its authority.
        if (selectedAuthority) selectedPlaceId = null;
        void render();
      });
      panel.appendChild(button);
    }
  }

  async function fetchChoropleth(
    key: string,
  ): Promise<{
    featureCollection: GeoJSON.FeatureCollection;
    contextLabel: string;
    contextKey: string;
  }> {
    if (drillPlaceId) {
      const url =
        `${apiBase}/v1/place/${encodeURIComponent(drillPlaceId)}/children/geometry` +
        `?indicator=${encodeURIComponent(key)}&child_type=lsoa21`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return {
        featureCollection: (await res.json()) as GeoJSON.FeatureCollection,
        contextLabel: `neighbourhoods in ${drillName ?? "this area"}`,
        contextKey: `drill:${drillPlaceId}`,
      };
    }

    const level = defaultLevel(key);
    const large = level === "lsoa21" ? "&large=true" : "";
    const url =
      `${apiBase}/v1/geographies/${encodeURIComponent(level)}/geometry` +
      `?indicator=${encodeURIComponent(key)}${large}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return {
      featureCollection: (await res.json()) as GeoJSON.FeatureCollection,
      contextLabel: `${LEVEL_LABELS[level] ?? level} areas`,
      contextKey: `national:${level}`,
    };
  }

  async function fetchProvision(): Promise<{
    points?: GeoJSON.FeatureCollection;
    error?: string;
  }> {
    const overlayKeys = selectedOverlayKeys();
    if (!drillPlaceId || overlayKeys.length === 0) return {};

    try {
      const res = await fetch(
        `${apiBase}/v1/place/${encodeURIComponent(drillPlaceId)}/amenities/geometry?indicators=${encodeURIComponent(overlayKeys.join(","))}`,
      );
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return { points: (await res.json()) as GeoJSON.FeatureCollection };
    } catch (error) {
      return {
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  async function render(): Promise<void> {
    const key = indicatorSel!.value;
    if (!key) return;

    if (backBtn) backBtn.hidden = !drillPlaceId;
    syncOverlayControls();
    if (status) status.textContent = "Loading…";

    let choropleth: Awaited<ReturnType<typeof fetchChoropleth>>;
    let provision: Awaited<ReturnType<typeof fetchProvision>>;
    try {
      [choropleth, provision] = await Promise.all([
        fetchChoropleth(key),
        fetchProvision(),
      ]);
    } catch (error) {
      if (status) {
        status.textContent =
          "Could not load map: " +
          (error instanceof Error ? error.message : String(error));
      }
      return;
    }

    const map = await getMap();
    const shouldFit = currentContextKey !== choropleth.contextKey;

    await map.setChoropleth({
      featureCollection: choropleth.featureCollection,
      valueKey: "value",
      label: byKey.get(key)?.label ?? key,
      indicatorKey: key,
      fit: shouldFit,
    });
    currentContextKey = choropleth.contextKey;
    map.setSelectedPlaceId(selectedPlaceId);
    await map.setAmenityPoints(provision.points);

    const n = (choropleth.featureCollection.features ?? []).filter(
      (feature) => typeof feature.properties?.value === "number",
    ).length;
    const overlayKeys = selectedOverlayKeys();
    const pointCount = provision.points?.features.length ?? 0;
    const overlayStatus =
      overlayKeys.length > 0
        ? provision.error
          ? ` · provision layer unavailable (${provision.error})`
          : ` · ${pointCount} provision points`
        : "";

    if (status) {
      status.textContent =
        `${n} ${choropleth.contextLabel} with data${overlayStatus} · click an area for details`;
    }
  }

  indicatorSel.addEventListener("change", () => {
    drillPlaceId = null;
    drillName = null;
    selectedPlaceId = null;
    currentContextKey = null;
    resetPanel();
    void getMap().then((map) => map.clearSelection());
    void render();
  });

  backBtn?.addEventListener("click", () => {
    drillPlaceId = null;
    drillName = null;
    selectedPlaceId = null;
    currentContextKey = null;
    resetPanel();
    void getMap().then((map) => map.clearSelection());
    void render();
  });

  for (const input of overlayInputs) {
    input.addEventListener("change", () => void render());
  }

  window.addEventListener(
    "pagehide",
    () => {
      void mapPromise?.then((map) => map.destroy());
    },
    { once: true },
  );

  void render();
}

init();
