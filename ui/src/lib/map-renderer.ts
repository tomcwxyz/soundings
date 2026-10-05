// MapLibre GL JS map rendering helpers for Soundings.
//
// All functions are browser-only: MapLibre requires WebGL, so callers must
// dynamic-import this module on the client side (Astro `<script type="module">`
// or an SSE-driven `import()` when a map block arrives).
//
// Design tokens (Good Ship):
//   --color-accent  #4a7c59  (green)
//   --color-primary #1a2f4e  (navy)
//   --color-bg      #faf9f6  (cream)

import maplibregl, { type Popup } from "maplibre-gl";
import { PALETTE } from "./chart";

const ACCENT_GREEN = "#4a7c59";
const CREAM = "#faf9f6";
const NAVY = "#1a2f4e";
// Neutral fill for choropleths with no data — avoids painting a misleading
// dark uniform map (and a fake 0→1 legend) when no feature has a value, e.g.
// a peer choropleth of a passthrough indicator not stored per-area.
const NO_DATA_FILL = "#e4e1da";

/** True when at least one value is a finite number. Drives whether a
 *  choropleth renders a real colour ramp or degrades to a neutral map. */
export function hasFiniteValues(
  values: Array<number | null | undefined>,
): boolean {
  return values.some((v) => typeof v === "number" && Number.isFinite(v));
}

/** Map each value to its rank position in [0, 1] (smallest → 0, largest → 1),
 *  preserving input order; non-finite entries map to null. Used to colour
 *  choropleths by rank/quantile instead of raw value, so heavily-skewed
 *  indicators still render with even contrast. */
export function rankFractions(
  values: Array<number | null | undefined>,
): Array<number | null> {
  const out: Array<number | null> = values.map(() => null);
  const finite = values
    .map((v, i) => ({ v, i }))
    .filter(
      (x): x is { v: number; i: number } =>
        typeof x.v === "number" && Number.isFinite(x.v),
    );
  const n = finite.length;
  if (n === 0) return out;
  if (n === 1) {
    out[finite[0].i] = 0.5;
    return out;
  }
  finite
    .sort((a, b) => a.v - b.v)
    .forEach((x, pos) => {
      out[x.i] = pos / (n - 1);
    });
  return out;
}

export interface ChoroplethSemantics {
  reverse: boolean;
  lowValueLabel: string;
  highValueLabel: string;
  note: string;
}

/** Describe how an indicator should read on a choropleth. Rank colouring is
 * deliberately neutral for most indicators, but some deciles run in the
 * opposite direction: a lower number represents greater need/priority. */
export function choroplethSemantics(indicatorKey?: string): ChoroplethSemantics {
  if (
    indicatorKey?.startsWith("food.ppfi.") ||
    indicatorKey === "deprivation.imd.decile"
  ) {
    return {
      reverse: true,
      lowValueLabel: "Highest priority",
      highValueLabel: indicatorKey.startsWith("food.ppfi.")
        ? "Lowest priority"
        : "Least deprived",
      note: indicatorKey.startsWith("food.ppfi.")
        ? "PPFI decile 1 is highest priority. Colours show relative rank."
        : "IMD decile 1 is most deprived. Colours show relative rank.",
    };
  }
  return {
    reverse: false,
    lowValueLabel: "Lower value",
    highValueLabel: "Higher value",
    note: "Colours show relative rank, not equal value intervals.",
  };
}

/** Rank values for choropleth display, reversing the visual direction for
 * indicators where a lower numeric value means greater priority/need. */
export function choroplethRankFractions(
  values: Array<number | null | undefined>,
  indicatorKey?: string,
): Array<number | null> {
  const ranks = rankFractions(values);
  if (!choroplethSemantics(indicatorKey).reverse) return ranks;
  return ranks.map((rank) => (rank === null ? null : 1 - rank));
}

// Shared base-map options: no tile source (solid background via CSS), no
// rotation, zoom controls in the bottom-right. When `tilesUrl` is provided, a
// raster OSM base layer is added; otherwise the map renders tile-less (as
// before) so existing tests and SSR keep working unchanged.
function baseMapOptions(
  container: HTMLElement,
  tilesUrl?: string,
): maplibregl.MapOptions {
  const sources: Record<string, any> = {};
  const layers: any[] = [];
  if (tilesUrl) {
    sources["base-tiles"] = {
      type: "raster",
      tiles: [tilesUrl],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    };
    layers.push({ id: "base", type: "raster", source: "base-tiles" });
  }
  return {
    container,
    // Minimal blank style (no external tiles — local-first). `glyphs` must be
    // omitted entirely: setting it to `undefined` fails MapLibre's style
    // validation ("glyphs: string expected, undefined found"), which aborts
    // the style load so the boundary layers are never added.
    style: {
      version: 8,
      sources,
      layers,
    },
    attributionControl: tilesUrl ? {} : false,
    dragRotate: false,
    touchZoomRotate: false,
    keyboard: false,
  };
}

function featureBounds(geojson: GeoJSON.GeoJSON): maplibregl.LngLatBoundsLike {
  // Compute bounds directly. (A previous version constructed a standalone
  // maplibregl.GeoJSONSource to do this, but that constructor needs a map
  // context and threw here — which aborted the load handler before fitBounds
  // ran, leaving the camera at world zoom 0 and the boundary an invisible
  // speck.) computeBounds handles Polygon/MultiPolygon/FeatureCollection.
  return computeBounds(geojson);
}

/** [min, mid, max] of the finite values, for a choropleth colour ramp.
 *  Falls back to [0, 0.5, 1] when there are no finite values. */
export function colourDomain(
  values: Array<number | null | undefined>,
): [number, number, number] {
  const finite = values.filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  if (finite.length === 0) return [0, 0.5, 1];
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  return [min, (min + max) / 2, max];
}

function computeBounds(geojson: GeoJSON.GeoJSON): maplibregl.LngLatBoundsLike {
  let minLng = Infinity;
  let minLat = Infinity;
  let maxLng = -Infinity;
  let maxLat = -Infinity;

  const walkCoords = (coords: number[][]) => {
    for (const c of coords) {
      if (Array.isArray(c[0])) {
        walkCoords(c as unknown as number[][]);
      } else if (typeof c[0] === "number" && typeof c[1] === "number") {
        const [lng, lat] = c;
        if (lng < minLng) minLng = lng;
        if (lat < minLat) minLat = lat;
        if (lng > maxLng) maxLng = lng;
        if (lat > maxLat) maxLat = lat;
      }
    }
  };

  const visit = (geom: GeoJSON.Geometry | undefined) => {
    if (!geom) return;
    switch (geom.type) {
      case "Point":
        walkCoords([geom.coordinates as unknown as number[]]);
        break;
      case "MultiPoint":
      case "LineString":
        walkCoords(geom.coordinates as unknown as number[][]);
        break;
      case "MultiLineString":
      case "Polygon":
        walkCoords(geom.coordinates as unknown as number[][]);
        break;
      case "MultiPolygon":
        for (const poly of geom.coordinates as unknown as number[][][][]) {
          walkCoords(poly as unknown as number[][]);
        }
        break;
      case "GeometryCollection":
        for (const g of geom.geometries) visit(g);
        break;
    }
  };

  if (geojson.type === "Feature") {
    visit(geojson.geometry);
  } else if (geojson.type === "FeatureCollection") {
    for (const f of geojson.features) visit(f.geometry);
  } else {
    visit(geojson);
  }

  if (!isFinite(minLng)) {
    return [[-1, 51], [1, 52]]; // fallback around England centroid
  }
  return [[minLng, minLat], [maxLng, maxLat]];
}

export interface RenderPlaceMapOptions {
  label?: string;
  tilesUrl?: string;
}

/**
 * Render a single GeoJSON Feature as a filled polygon on a tile-less map.
 * Returns a cleanup function that calls `map.remove()`.
 */
export function renderPlaceMap(
  container: HTMLElement,
  geojson: GeoJSON.Feature,
  options: RenderPlaceMapOptions = {},
): () => void {
  const map = new maplibregl.Map(
    baseMapOptions(container, options.tilesUrl),
  );

  const sourceId = "place";
  const fillId = "place-fill";
  const outlineId = "place-outline";

  map.on("load", () => {
    map.addSource(sourceId, { type: "geojson", data: geojson });

    map.addLayer({
      id: fillId,
      type: "fill",
      source: sourceId,
      paint: {
        "fill-color": ACCENT_GREEN,
        "fill-opacity": 0.3,
      },
    });

    map.addLayer({
      id: outlineId,
      type: "line",
      source: sourceId,
      paint: {
        "line-color": ACCENT_GREEN,
        "line-width": 1,
      },
    });

    map.fitBounds(featureBounds(geojson), { padding: 20 });
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

  return () => map.remove();
}

export interface RenderChoroplethMapOptions {
  /** Two-stop colour scale [low, high]. Default: cream → navy via green. */
  colourScale?: [string, string];
  label?: string;
  /** Catalogue key used to apply indicator-specific map semantics. */
  indicatorKey?: string;
  /** Keep an existing area highlighted when the map is rebuilt. */
  selectedPlaceId?: string;
  tilesUrl?: string;
  /** Optional amenity point layers to overlay on top of the choropleth
   *  (e.g. food banks over a deprivation map). Toggleable via the legend. */
  points?: GeoJSON.FeatureCollection;
  /** When set, an area click calls this (for a side panel) instead of showing
   *  the built-in "View place" pop-up. Used by the explorer. */
  onSelectArea?: (sel: { placeId?: string; name: string; value: unknown }) => void;
}

/** HTML for a click pop-up on a choropleth area: place name, the indicator
 *  value, and a "View place →" link to its profile page. The link is omitted
 *  when no place id is available. */
export function placePopupHtml(opts: {
  name: string;
  label: string;
  value: string;
  placeId?: string;
}): string {
  const link = opts.placeId
    ? `<br/><a href="/place/${encodeURIComponent(opts.placeId)}" ` +
      `style="color:#4a7c59;font-weight:600;text-decoration:none">View place →</a>`
    : "";
  return (
    `<div style="font-family:system-ui,sans-serif;font-size:13px;line-height:1.5">` +
    `<strong>${escapeHtml(opts.name)}</strong><br/>` +
    `${escapeHtml(opts.label)}: ${escapeHtml(opts.value)}${link}</div>`
  );
}

/**
 * Render a FeatureCollection as a choropleth, interpolating fill colour from
 * the `valueKey` property on each feature. Hover shows place name + value.
 * Returns a cleanup function that calls `map.remove()`.
 */
export function renderChoroplethMap(
  container: HTMLElement,
  featureCollection: GeoJSON.FeatureCollection,
  valueKey: string,
  options: RenderChoroplethMapOptions = {},
): () => void {
  const map = new maplibregl.Map(
    baseMapOptions(container, options.tilesUrl),
  );

  const sourceId = "choropleth";
  const fillId = "choropleth-fill";

  // Default three-stop ramp: cream → green → navy.
  const stops: [string, string] = options.colourScale ?? [CREAM, NAVY];

  // Real value domain (for the legend) + rank-normalised colour input.
  const values = featureCollection.features.map(
    (f) => (f.properties?.[valueKey] as number | null | undefined),
  );
  const [domMin, , domMax] = colourDomain(values);
  const hasData = hasFiniteValues(values);

  // Colour by rank, not raw value: geographic indicators are often heavily
  // skewed (one rural outlier dwarfs the rest), so a linear value ramp crushes
  // most areas into a single shade. Rank/quantile colouring spreads contrast
  // evenly. Each feature carries its rank in [0,1] as `__rank`.
  const semantics = choroplethSemantics(options.indicatorKey ?? valueKey);
  const ranks = choroplethRankFractions(values, options.indicatorKey ?? valueKey);
  const RANK_KEY = "__rank";
  const PLACE_ID_KEY = "__place_id";
  featureCollection.features.forEach((f, i) => {
    if (!f.properties) f.properties = {};
    if (ranks[i] != null) {
      f.properties[RANK_KEY] = ranks[i];
    }
    const placeId =
      (f.properties.id as string | undefined) ??
      (f.properties.place_id as string | undefined);
    if (placeId) f.properties[PLACE_ID_KEY] = placeId;
  });

  // No data anywhere → neutral fill (no misleading ramp). Otherwise interpolate
  // on rank; features without a value fall back to the neutral fill.
  const fillColor = (
    !hasData
      ? NO_DATA_FILL
      : [
          "case",
          ["==", ["typeof", ["get", RANK_KEY]], "number"],
          [
            "interpolate",
            ["linear"],
            ["get", RANK_KEY],
            0,
            stops[0],
            0.5,
            ACCENT_GREEN,
            1,
            stops[1],
          ],
          NO_DATA_FILL,
        ]
  ) as unknown as maplibregl.ExpressionSpecification;

  // Legend built up front (referenced by the async load handler below). The
  // choropleth gradient row is added only when there's data; amenity swatch
  // rows are appended once the point layers load.
  const legend = document.createElement("div");
  legend.className = "map-legend choropleth-legend";
  if (hasData) {
    const leftValue = semantics.reverse ? domMax : domMin;
    const rightValue = semantics.reverse ? domMin : domMax;
    const leftMeaning = semantics.reverse
      ? semantics.highValueLabel
      : semantics.lowValueLabel;
    const rightMeaning = semantics.reverse
      ? semantics.lowValueLabel
      : semantics.highValueLabel;
    legend.innerHTML =
      `<span class="legend-label">${escapeHtml(options.label ?? valueKey)}</span>` +
      `<span class="legend-gradient"></span>` +
      `<span class="legend-scale">` +
      `<span><strong>${leftValue.toLocaleString("en-GB")}</strong><small>${escapeHtml(leftMeaning)}</small></span>` +
      `<span class="legend-scale-right"><strong>${rightValue.toLocaleString("en-GB")}</strong><small>${escapeHtml(rightMeaning)}</small></span>` +
      `</span>` +
      `<span class="legend-note">${escapeHtml(semantics.note)}</span>`;
  }

  const amenityPopup = options.points
    ? new maplibregl.Popup({ closeButton: false, closeOnClick: false })
    : null;

  map.on("load", () => {
    map.addSource(sourceId, { type: "geojson", data: featureCollection });

    map.addLayer({
      id: fillId,
      type: "fill",
      source: sourceId,
      paint: {
        "fill-color": fillColor,
        "fill-opacity": hasData ? 0.85 : 0.3,
      },
    });

    map.addLayer({
      id: "choropleth-outline",
      type: "line",
      source: sourceId,
      paint: {
        "line-color": "#ffffff",
        "line-width": 0.5,
      },
    });

    map.addLayer({
      id: "choropleth-selected",
      type: "line",
      source: sourceId,
      filter: [
        "==",
        ["get", PLACE_ID_KEY],
        options.selectedPlaceId ?? "__soundings_no_selection__",
      ],
      paint: {
        "line-color": NAVY,
        "line-width": 3,
      },
    });

    // Overlay amenity point layers on top of the choropleth (increment 4).
    if (options.points && amenityPopup) {
      const { legendItems, layerIds } = addAmenityPointLayers(map, options.points, amenityPopup);
      const amenityRows = buildAmenityLegend(legendItems, layerIds);
      while (amenityRows.firstChild) legend.appendChild(amenityRows.firstChild);
      wireLegendToggles(map, legend);
    }

    // Append the legend once its content is settled (gradient and/or amenity
    // rows). Skip only when there's nothing to show.
    if (legend.childNodes.length > 0 && !legend.parentElement) {
      container.appendChild(legend);
    }

    map.fitBounds(featureBounds(featureCollection), { padding: 20 });
  });

  // Hover popup with place name + value.
  const popup: Popup = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
  });

  let hoveredId: string | number | undefined;

  map.on("mousemove", fillId, (e) => {
    const features = map.queryRenderedFeatures(e.point, { layers: [fillId] });
    if (features.length === 0) return;
    const f = features[0];
    const props = (f.properties ?? {}) as Record<string, unknown>;
    const placeName =
      (props.name as string | undefined) ??
      (props.place_name as string | undefined) ??
      (props.place_id as string | undefined) ??
      "—";
    const rawValue = props[valueKey];
    const displayValue =
      typeof rawValue === "number" ? rawValue.toLocaleString("en-GB") : String(rawValue ?? "—");
    const label = options.label ?? valueKey;
    popup.setHTML(
      `<div style="font-family:system-ui,sans-serif"><strong>${escapeHtml(placeName)}</strong><br/>${escapeHtml(label)}: ${escapeHtml(displayValue)}</div>`,
    );
    const coords = (f.geometry as GeoJSON.Point | undefined)?.coordinates;
    if (coords) {
      popup.setLngLat(coords as [number, number]);
    } else {
      popup.setLngLat(e.lngLat);
    }
    popup.addTo(map);
    hoveredId = f.id;
    map.getCanvas().style.cursor = "pointer";
  });

  map.on("mouseleave", fillId, () => {
    popup.remove();
    hoveredId = undefined;
    map.getCanvas().style.cursor = "";
  });

  // Click pop-up: persists, with a "View place →" link to the area's profile.
  const clickPopup = new maplibregl.Popup({
    closeButton: true,
    closeOnClick: false,
    maxWidth: "260px",
  });
  map.on("click", fillId, (e) => {
    const features = map.queryRenderedFeatures(e.point, { layers: [fillId] });
    if (features.length === 0) return;
    const props = (features[0].properties ?? {}) as Record<string, unknown>;
    const placeName =
      (props.name as string | undefined) ??
      (props.place_name as string | undefined) ??
      (props.place_id as string | undefined) ??
      "—";
    const rawValue = props[valueKey];
    const displayValue =
      typeof rawValue === "number" ? rawValue.toLocaleString("en-GB") : String(rawValue ?? "—");
    const placeId =
      (props.id as string | undefined) ?? (props.place_id as string | undefined);

    if (placeId) {
      map.setFilter("choropleth-selected", [
        "==",
        ["get", PLACE_ID_KEY],
        placeId,
      ]);
    }

    // Explorer mode: notify a side panel instead of covering the map with a
    // pop-up. Otherwise show the built-in "View place" pop-up.
    if (options.onSelectArea) {
      options.onSelectArea({ placeId, name: placeName, value: rawValue });
      return;
    }
    clickPopup
      .setLngLat(e.lngLat)
      .setHTML(
        placePopupHtml({
          name: placeName,
          label: options.label ?? valueKey,
          value: displayValue,
          placeId,
        }),
      )
      .addTo(map);
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

  return () => {
    popup.remove();
    clickPopup.remove();
    amenityPopup?.remove();
    legend.remove();
    map.remove();
  };
}

/** HTML for a click pop-up on an amenity point: its name, type, and address
 *  (when the feature carries one). */
export function amenityPopupHtml(opts: {
  name: string;
  type: string;
  address?: string;
}): string {
  return (
    `<div style="font-family:system-ui,sans-serif;font-size:13px;line-height:1.5">` +
    `<strong>${escapeHtml(opts.name)}</strong><br/>${escapeHtml(opts.type)}` +
    `${opts.address ? `<br/>${escapeHtml(opts.address)}` : ""}</div>`
  );
}

/** "infrastructure.food_banks_count" → "Food banks". */
export function amenityLayerLabel(indicatorKey: string): string {
  const base = indicatorKey.replace(/^infrastructure\./, "").replace(/_count$/, "");
  const words = base.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function amenityLegendItems(
  layers: string[],
): Array<{ label: string; colour: string }> {
  return layers.map((layer, i) => ({
    label: amenityLayerLabel(layer),
    colour: PALETTE[i % PALETTE.length],
  }));
}

/**
 * Add one colour-coded circle layer per distinct amenity `layer` property in
 * `points`, with hover name popups. Assumes the map's style is loaded (call
 * inside a `load` handler). Returns the legend items so the caller can render
 * a combined legend. Shared by the points-only and choropleth+points maps.
 */
function addAmenityPointLayers(
  map: maplibregl.Map,
  points: GeoJSON.FeatureCollection,
  popup: Popup,
): { legendItems: Array<{ label: string; colour: string }>; layerIds: string[] } {
  const layers = Array.from(
    new Set(points.features.map((f) => String((f.properties ?? {}).layer ?? ""))),
  ).filter(Boolean);
  const legendItems = amenityLegendItems(layers);
  const colourByLayer = new Map(legendItems.map((it, i) => [layers[i], it.colour]));

  // Persistent pop-up on click (hover gives the quick peek). Cleaned up with
  // the map on teardown.
  const clickPopup = new maplibregl.Popup({
    closeButton: true,
    closeOnClick: false,
    maxWidth: "240px",
  });

  map.addSource("amenities", { type: "geojson", data: points });
  const layerIds: string[] = [];
  for (const layer of layers) {
    const id = `amenity-${layer}`;
    layerIds.push(id);
    map.addLayer({
      id,
      type: "circle",
      source: "amenities",
      filter: ["==", ["get", "layer"], layer],
      paint: {
        "circle-radius": 5,
        "circle-color": colourByLayer.get(layer) ?? NAVY,
        "circle-stroke-color": CREAM,
        "circle-stroke-width": 1,
      },
    });
    map.on("click", id, (e) => {
      const f = e.features?.[0];
      const props = (f?.properties ?? {}) as Record<string, unknown>;
      const name = (props.name as string | undefined) ?? amenityLayerLabel(layer);
      const address =
        (props.address as string | undefined) ?? (props.postcode as string | undefined);
      const coords = (f?.geometry as GeoJSON.Point | undefined)?.coordinates;
      if (coords) {
        clickPopup
          .setLngLat(coords as [number, number])
          .setHTML(amenityPopupHtml({ name, type: amenityLayerLabel(layer), address }))
          .addTo(map);
      }
    });
    map.on("mouseenter", id, (e) => {
      const f = e.features?.[0];
      const props = (f?.properties ?? {}) as Record<string, unknown>;
      const name = (props.name as string | undefined) ?? amenityLayerLabel(layer);
      const coords = (f?.geometry as GeoJSON.Point | undefined)?.coordinates;
      popup.setHTML(
        `<div style="font-family:system-ui,sans-serif"><strong>${escapeHtml(name)}</strong><br/>${escapeHtml(amenityLayerLabel(layer))}</div>`,
      );
      if (coords) popup.setLngLat(coords as [number, number]).addTo(map);
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", id, () => {
      popup.remove();
      map.getCanvas().style.cursor = "";
    });
  }
  return { legendItems, layerIds };
}

/** Build a legend element: one clickable swatch row per amenity layer (click
 *  toggles that layer). Returns the element; wiring the toggles is the caller's
 *  job via `layerIds`. */
export function buildAmenityLegend(
  legendItems: Array<{ label: string; colour: string }>,
  layerIds: string[],
): HTMLElement {
  const legend = document.createElement("div");
  legend.className = "map-legend amenity-legend";
  legend.innerHTML = legendItems
    .map(
      (it, i) =>
        `<button type="button" class="legend-item legend-toggle" data-layer-id="${escapeHtml(layerIds[i] ?? "")}">` +
        `<span class="legend-swatch" style="background:${it.colour}"></span>${escapeHtml(it.label)}</button>`,
    )
    .join("");
  return legend;
}

/** Wire click-to-toggle visibility on each amenity legend button. */
function wireLegendToggles(map: maplibregl.Map, legend: HTMLElement): void {
  for (const btn of Array.from(legend.querySelectorAll<HTMLButtonElement>(".legend-toggle"))) {
    btn.addEventListener("click", () => {
      const id = btn.dataset.layerId;
      if (!id) return;
      const visible = map.getLayoutProperty(id, "visibility") !== "none";
      map.setLayoutProperty(id, "visibility", visible ? "none" : "visible");
      btn.classList.toggle("is-off", visible);
    });
  }
}

/**
 * Render a place boundary plus one colour-coded circle layer per amenity
 * `layer` property in `points`, with a legend and name popups. Returns a
 * cleanup function.
 */
export function renderAmenityMap(
  container: HTMLElement,
  boundary: GeoJSON.Feature,
  points: GeoJSON.FeatureCollection,
  options: { tilesUrl?: string } = {},
): () => void {
  const map = new maplibregl.Map(baseMapOptions(container, options.tilesUrl));
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
  let legend: HTMLElement | null = null;

  map.on("load", () => {
    map.addSource("boundary", { type: "geojson", data: boundary });
    map.addLayer({
      id: "boundary-fill",
      type: "fill",
      source: "boundary",
      paint: { "fill-color": ACCENT_GREEN, "fill-opacity": 0.08 },
    });
    map.addLayer({
      id: "boundary-outline",
      type: "line",
      source: "boundary",
      paint: { "line-color": ACCENT_GREEN, "line-width": 1 },
    });

    const { legendItems, layerIds } = addAmenityPointLayers(map, points, popup);
    legend = buildAmenityLegend(legendItems, layerIds);
    container.appendChild(legend);
    wireLegendToggles(map, legend);

    map.fitBounds(featureBounds(boundary), { padding: 20 });
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

  return () => {
    popup.remove();
    legend?.remove();
    map.remove();
  };
}

/**
 * Render a place boundary plus a single circle layer of organisation (charity)
 * registered-address points, sized by income. Click popups show name, income,
 * cause, and a link to the Charity Commission register page. Returns a
 * cleanup function.
 */
export function renderOrganisationMap(
  container: HTMLElement,
  boundary: GeoJSON.Feature,
  points: GeoJSON.FeatureCollection,
  options: { tilesUrl?: string } = {},
): () => void {
  const map = new maplibregl.Map(baseMapOptions(container, options.tilesUrl));
  let legend: HTMLElement | null = null;

  map.on("load", () => {
    map.addSource("boundary", { type: "geojson", data: boundary });
    map.addLayer({
      id: "boundary-fill",
      type: "fill",
      source: "boundary",
      paint: { "fill-color": ACCENT_GREEN, "fill-opacity": 0.08 },
    });
    map.addLayer({
      id: "boundary-outline",
      type: "line",
      source: "boundary",
      paint: { "line-color": ACCENT_GREEN, "line-width": 1 },
    });

    if (points.features.length > 0) {
      map.addSource("org-points", { type: "geojson", data: points });
      map.addLayer({
        id: "org-points",
        type: "circle",
        source: "org-points",
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["get", "income"],
            0, 4,
            100000, 5,
            1000000, 7,
            10000000, 9,
          ],
          "circle-color": NAVY,
          "circle-stroke-color": CREAM,
          "circle-stroke-width": 1,
          "circle-opacity": 0.8,
        },
      });

      const clickPopup = new maplibregl.Popup({
        closeButton: true,
        closeOnClick: false,
        maxWidth: "260px",
      });

      map.on("click", "org-points", (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const props = (f.properties ?? {}) as Record<string, unknown>;
        const name = (props.name as string | undefined) ?? "—";
        const incomeRaw = props.income;
        const income =
          typeof incomeRaw === "number" && Number.isFinite(incomeRaw)
            ? incomeRaw.toLocaleString("en-GB", {
                style: "currency",
                currency: "GBP",
                maximumFractionDigits: 0,
              }) + "/yr"
            : "Income not reported";
        const cause = (props.cause as string | undefined) ?? "";
        const registerUrl = (props.register_url as string | undefined) ?? "";
        const html =
          `<div style="font-family:system-ui,sans-serif;font-size:13px;line-height:1.5">` +
          `<strong>${escapeHtml(name)}</strong><br/>${escapeHtml(income)}` +
          (cause
            ? `<br/><span class="text-muted">${escapeHtml(cause)}</span>`
            : "") +
          (registerUrl
            ? `<br/><a href="${escapeHtml(registerUrl)}" target="_blank">Register page →</a>`
            : "") +
          `</div>`;
        const coords = (f.geometry as GeoJSON.Point | undefined)?.coordinates;
        if (coords) {
          clickPopup
            .setLngLat(coords as [number, number])
            .setHTML(html)
            .addTo(map);
        }
      });

      map.on("mouseenter", "org-points", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "org-points", () => {
        map.getCanvas().style.cursor = "";
      });

      // Legend entry for charities.
      legend = document.createElement("div");
      legend.className = "map-legend org-legend";
      legend.innerHTML =
        '<div class="legend-item">' +
        '<span class="legend-swatch" style="background:#1a2f4e;border-radius:50%"></span>' +
        " Charities</div>";
      container.appendChild(legend);
    }

    map.fitBounds(featureBounds(boundary), { padding: 20 });
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

  return () => {
    legend?.remove();
    map.remove();
  };
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
