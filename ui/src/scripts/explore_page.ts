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
  const mapSurface = surface;

  const apiBase = mapSurface.dataset.apiBase || "";
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
  let currentSelection: { placeId?: string; name: string; value?: unknown } | null = null;
  const comparisonPlaces = new Map<string, { name: string; value: unknown }>();
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

  function comparisonIds(): string[] {
    return Array.from(comparisonPlaces.keys());
  }

  async function syncComparisonHighlight(): Promise<void> {
    const map = await getMap();
    map.setComparisonPlaceIds(comparisonIds());
  }

  function clearComparison(): void {
    comparisonPlaces.clear();
    void syncComparisonHighlight();
  }

  function comparisonAskHref(): string {
    const names = Array.from(comparisonPlaces.values()).map((place) => place.name);
    const scope = drillName ? ` within ${drillName}` : "";
    const indicator = labelFor(indicatorSel!.value);
    const question =
      `Compare ${names.join(", ")}${scope} for ${indicator}. What stands out, where do they differ, and what should we pay attention to?`;
    return "/ask?q=" + encodeURIComponent(question);
  }

  function renderComparisonSummary(): void {
    if (!panel || comparisonPlaces.size === 0) return;

    const section = document.createElement("section");
    section.className = "comparison-summary";
    const heading = document.createElement("h3");
    heading.textContent = `Comparison set (${comparisonPlaces.size}/5)`;
    section.appendChild(heading);

    const list = document.createElement("ul");
    list.className = "comparison-list";
    for (const [id, place] of comparisonPlaces) {
      const item = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = place.name;
      item.appendChild(label);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "comparison-remove secondary";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        comparisonPlaces.delete(id);
        void syncComparisonHighlight();
        if (currentSelection) {
          void showPanel(currentSelection);
        } else {
          resetPanel();
        }
      });
      item.appendChild(remove);
      list.appendChild(item);
    }
    section.appendChild(list);

    if (comparisonPlaces.size >= 2) {
      const actions = document.createElement("div");
      actions.className = "comparison-actions";

      const compare = document.createElement("a");
      compare.className = "panel-link";
      compare.href =
        "/compare?places=" +
        encodeURIComponent(comparisonIds().join(",")) +
        "&indicators=" +
        encodeURIComponent(indicatorSel!.value) +
        "&basis=absolute";
      compare.textContent = "Compare selected →";
      actions.appendChild(compare);

      const ask = document.createElement("a");
      ask.className = "panel-link";
      ask.href = comparisonAskHref();
      ask.textContent = "Ask about selected →";
      actions.appendChild(ask);

      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "comparison-clear secondary";
      clear.textContent = "Clear";
      clear.addEventListener("click", () => {
        clearComparison();
        if (currentSelection) {
          void showPanel(currentSelection);
        } else {
          resetPanel();
        }
      });
      actions.appendChild(clear);

      section.appendChild(actions);
    }

    panel.appendChild(section);
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
          new InteractiveMap(mapSurface, {
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
    value?: unknown;
  }): Promise<void> {
    if (!panel) return;

    currentSelection = selection;
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

    if (
      drillPlaceId &&
      selection.placeId?.startsWith("lsoa21:")
    ) {
      const compareButton = document.createElement("button");
      compareButton.type = "button";
      compareButton.className = "panel-drill secondary";
      const alreadySelected = comparisonPlaces.has(selection.placeId);
      compareButton.textContent = alreadySelected
        ? "Remove from comparison"
        : comparisonPlaces.size >= 5
          ? "Comparison set full"
          : "Add to comparison";
      compareButton.disabled = !alreadySelected && comparisonPlaces.size >= 5;
      compareButton.addEventListener("click", () => {
        if (!selection.placeId) return;
        if (comparisonPlaces.has(selection.placeId)) {
          comparisonPlaces.delete(selection.placeId);
        } else if (comparisonPlaces.size < 5) {
          comparisonPlaces.set(selection.placeId, {
            name: selection.name,
            value: selection.value,
          });
        }
        void syncComparisonHighlight();
        void showPanel(selection);
      });
      panel.appendChild(compareButton);
    }

    renderComparisonSummary();
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
    map.setComparisonPlaceIds(comparisonIds());
    await map.setAmenityPoints(provision.points);

    const n = (choropleth.featureCollection.features ?? []).filter(
      (feature) => typeof feature.properties?.value === "number",
    ).length;
    const overlayKeys = selectedOverlayKeys();
    const pointCount = provision.points?.features.length ?? 0;
    const overlayStatus =
      drillPlaceId && overlayKeys.length > 0
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
    const supportsCurrentDrill =
      !drillPlaceId ||
      (byKey.get(indicatorSel!.value)?.available_at.includes("lsoa21") ?? false);

    selectedPlaceId = null;
    currentSelection = null;

    if (!supportsCurrentDrill) {
      drillPlaceId = null;
      drillName = null;
      comparisonPlaces.clear();
      currentContextKey = null;
    }

    resetPanel();
    void getMap().then((map) => {
      map.clearSelection();
      map.setComparisonPlaceIds(comparisonIds());
    });
    void render();
  });

  backBtn?.addEventListener("click", () => {
    drillPlaceId = null;
    drillName = null;
    selectedPlaceId = null;
    currentSelection = null;
    comparisonPlaces.clear();
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
