// Stateful MapLibre wrapper for the Soundings explorer.
//
// The existing map-renderer.ts functions remain the stable, stateless renderers
// used by Ask/place pages. InteractiveMap is deliberately introduced first for
// /explore, where retaining camera, selection and point-layer state materially
// improves the interaction.

import maplibregl from "maplibre-gl";
import { PALETTE } from "./chart";
import {
  amenityLayerLabel,
  amenityLegendItems,
  amenityPopupHtml,
  choroplethRankFractions,
  choroplethSemantics,
  colourDomain,
  hasFiniteValues,
} from "./map-renderer";

const ACCENT_GREEN = "#4a7c59";
const CREAM = "#faf9f6";
const NAVY = "#1a2f4e";
const NO_DATA_FILL = "#e4e1da";

const CHOROPLETH_SOURCE = "interactive-choropleth";
const CHOROPLETH_FILL = "interactive-choropleth-fill";
const CHOROPLETH_OUTLINE = "interactive-choropleth-outline";
const CHOROPLETH_SELECTED = "interactive-choropleth-selected";
const AMENITY_SOURCE = "interactive-amenities";

const RANK_KEY = "__rank";
const PLACE_ID_KEY = "__place_id";

export interface AreaSelection {
  placeId?: string;
  name: string;
  value: unknown;
}

export interface InteractiveMapOptions {
  tilesUrl?: string;
  onSelectArea?: (selection: AreaSelection) => void;
}

export interface ChoroplethState {
  featureCollection: GeoJSON.FeatureCollection;
  valueKey: string;
  label: string;
  indicatorKey: string;
  fit?: boolean;
}

export function prepareChoroplethFeatureCollection(
  featureCollection: GeoJSON.FeatureCollection,
  valueKey: string,
  indicatorKey: string,
): GeoJSON.FeatureCollection {
  const values = featureCollection.features.map(
    (feature) => feature.properties?.[valueKey] as number | null | undefined,
  );
  const ranks = choroplethRankFractions(values, indicatorKey);

  return {
    ...featureCollection,
    features: featureCollection.features.map((feature, index) => {
      const properties = { ...(feature.properties ?? {}) };
      const placeId =
        (properties.id as string | undefined) ??
        (properties.place_id as string | undefined);
      if (ranks[index] !== null && ranks[index] !== undefined) {
        properties[RANK_KEY] = ranks[index];
      } else {
        delete properties[RANK_KEY];
      }
      if (placeId) properties[PLACE_ID_KEY] = placeId;
      return { ...feature, properties };
    }),
  };
}

function baseMapOptions(
  container: HTMLElement,
  tilesUrl?: string,
): maplibregl.MapOptions {
  const sources: Record<string, maplibregl.SourceSpecification> = {};
  const layers: maplibregl.LayerSpecification[] = [];

  if (tilesUrl) {
    sources["base-tiles"] = {
      type: "raster",
      tiles: [tilesUrl],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    };
    layers.push({
      id: "base",
      type: "raster",
      source: "base-tiles",
    });
  }

  return {
    container,
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

function computeBounds(geojson: GeoJSON.GeoJSON): maplibregl.LngLatBoundsLike {
  let minLng = Infinity;
  let minLat = Infinity;
  let maxLng = -Infinity;
  let maxLat = -Infinity;

  const visitCoords = (coords: unknown): void => {
    if (!Array.isArray(coords)) return;
    if (
      coords.length >= 2 &&
      typeof coords[0] === "number" &&
      typeof coords[1] === "number"
    ) {
      const lng = coords[0];
      const lat = coords[1];
      minLng = Math.min(minLng, lng);
      minLat = Math.min(minLat, lat);
      maxLng = Math.max(maxLng, lng);
      maxLat = Math.max(maxLat, lat);
      return;
    }
    for (const child of coords) visitCoords(child);
  };

  const visitGeometry = (geometry: GeoJSON.Geometry | null): void => {
    if (!geometry) return;
    if (geometry.type === "GeometryCollection") {
      for (const child of geometry.geometries) visitGeometry(child);
      return;
    }
    visitCoords(geometry.coordinates);
  };

  if (geojson.type === "FeatureCollection") {
    for (const feature of geojson.features) visitGeometry(feature.geometry);
  } else if (geojson.type === "Feature") {
    visitGeometry(geojson.geometry);
  } else {
    visitGeometry(geojson);
  }

  if (!Number.isFinite(minLng)) return [[-1, 51], [1, 52]];
  return [[minLng, minLat], [maxLng, maxLat]];
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function pointLayerId(layer: string): string {
  return "interactive-amenity-" + layer.replace(/[^a-zA-Z0-9_-]/g, "-");
}

export class InteractiveMap {
  private readonly map: maplibregl.Map;
  private readonly ready: Promise<void>;
  private readonly container: HTMLElement;
  private readonly onSelectArea?: (selection: AreaSelection) => void;
  private readonly hoverPopup = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
  });
  private readonly amenityHoverPopup = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
  });
  private readonly amenityClickPopup = new maplibregl.Popup({
    closeButton: true,
    closeOnClick: false,
    maxWidth: "260px",
  });
  private readonly legend: HTMLElement;

  private currentValueKey = "value";
  private currentLabel = "Value";
  private currentIndicatorKey = "";
  private selectedPlaceId: string | null = null;
  private choroplethInitialised = false;
  private hasFitted = false;
  private amenityLayerIds = new Map<string, string>();
  private amenityLayersBound = new Set<string>();
  private activeAmenityLayers: string[] = [];
  private choroplethLegendHtml = "";

  constructor(container: HTMLElement, options: InteractiveMapOptions = {}) {
    this.container = container;
    this.onSelectArea = options.onSelectArea;
    this.legend = document.createElement("div");
    this.legend.className = "map-legend choropleth-legend";

    this.map = new maplibregl.Map(baseMapOptions(container, options.tilesUrl));
    this.map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      "bottom-right",
    );

    this.ready = new Promise((resolve) => {
      this.map.on("load", () => resolve());
    });
  }

  async setChoropleth(state: ChoroplethState): Promise<void> {
    await this.ready;

    this.currentValueKey = state.valueKey;
    this.currentLabel = state.label;
    this.currentIndicatorKey = state.indicatorKey;

    const prepared = prepareChoroplethFeatureCollection(
      state.featureCollection,
      state.valueKey,
      state.indicatorKey,
    );
    const values = prepared.features.map(
      (feature) => feature.properties?.[state.valueKey] as number | null | undefined,
    );
    const hasData = hasFiniteValues(values);
    const semantics = choroplethSemantics(state.indicatorKey);

    if (!this.choroplethInitialised) {
      this.map.addSource(CHOROPLETH_SOURCE, {
        type: "geojson",
        data: prepared,
      });
      this.map.addLayer({
        id: CHOROPLETH_FILL,
        type: "fill",
        source: CHOROPLETH_SOURCE,
        paint: {
          "fill-color": NO_DATA_FILL,
          "fill-opacity": 0.3,
        },
      });
      this.map.addLayer({
        id: CHOROPLETH_OUTLINE,
        type: "line",
        source: CHOROPLETH_SOURCE,
        paint: {
          "line-color": "#ffffff",
          "line-width": 0.5,
        },
      });
      this.map.addLayer({
        id: CHOROPLETH_SELECTED,
        type: "line",
        source: CHOROPLETH_SOURCE,
        filter: [
          "==",
          ["get", PLACE_ID_KEY],
          "__soundings_no_selection__",
        ],
        paint: {
          "line-color": NAVY,
          "line-width": 3,
        },
      });
      this.bindAreaInteractions();
      this.choroplethInitialised = true;
    } else {
      const source = this.map.getSource(CHOROPLETH_SOURCE) as
        | maplibregl.GeoJSONSource
        | undefined;
      source?.setData(prepared);
    }

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
              CREAM,
              0.5,
              ACCENT_GREEN,
              1,
              NAVY,
            ],
            NO_DATA_FILL,
          ]
    ) as maplibregl.ExpressionSpecification | string;

    this.map.setPaintProperty(CHOROPLETH_FILL, "fill-color", fillColor);
    this.map.setPaintProperty(
      CHOROPLETH_FILL,
      "fill-opacity",
      hasData ? 0.85 : 0.3,
    );

    if (hasData) {
      const [min, , max] = colourDomain(values);
      const leftValue = semantics.reverse ? max : min;
      const rightValue = semantics.reverse ? min : max;
      const leftMeaning = semantics.reverse
        ? semantics.highValueLabel
        : semantics.lowValueLabel;
      const rightMeaning = semantics.reverse
        ? semantics.lowValueLabel
        : semantics.highValueLabel;

      this.choroplethLegendHtml =
        `<span class="legend-label">${escapeHtml(state.label)}</span>` +
        `<span class="legend-gradient"></span>` +
        `<span class="legend-scale">` +
        `<span><strong>${leftValue.toLocaleString("en-GB")}</strong><small>${escapeHtml(leftMeaning)}</small></span>` +
        `<span class="legend-scale-right"><strong>${rightValue.toLocaleString("en-GB")}</strong><small>${escapeHtml(rightMeaning)}</small></span>` +
        `</span>` +
        `<span class="legend-note">${escapeHtml(semantics.note)}</span>`;
    } else {
      this.choroplethLegendHtml =
        `<span class="legend-label">${escapeHtml(state.label)}</span>` +
        `<span class="legend-note">No values are available for this view.</span>`;
    }

    this.renderLegend();
    this.setSelectedPlaceId(this.selectedPlaceId);

    if (state.fit || !this.hasFitted) {
      this.map.fitBounds(computeBounds(prepared), {
        padding: 28,
        duration: this.hasFitted ? 350 : 0,
      });
      this.hasFitted = true;
    }
  }

  async setAmenityPoints(points?: GeoJSON.FeatureCollection): Promise<void> {
    await this.ready;
    const collection: GeoJSON.FeatureCollection = points ?? {
      type: "FeatureCollection",
      features: [],
    };
    const desiredLayers = Array.from(
      new Set(
        collection.features
          .map((feature) => String(feature.properties?.layer ?? ""))
          .filter(Boolean),
      ),
    );

    let source = this.map.getSource(AMENITY_SOURCE) as
      | maplibregl.GeoJSONSource
      | undefined;
    if (!source) {
      this.map.addSource(AMENITY_SOURCE, {
        type: "geojson",
        data: collection,
      });
      source = this.map.getSource(AMENITY_SOURCE) as maplibregl.GeoJSONSource;
    } else {
      source.setData(collection);
    }

    for (const [layer, id] of this.amenityLayerIds) {
      if (this.map.getLayer(id)) {
        this.map.setLayoutProperty(
          id,
          "visibility",
          desiredLayers.includes(layer) ? "visible" : "none",
        );
      }
    }

    const legendItems = amenityLegendItems(desiredLayers);
    desiredLayers.forEach((layer, index) => {
      let id = this.amenityLayerIds.get(layer);
      if (!id) {
        id = pointLayerId(layer);
        this.amenityLayerIds.set(layer, id);
      }

      if (!this.map.getLayer(id)) {
        this.map.addLayer({
          id,
          type: "circle",
          source: AMENITY_SOURCE,
          filter: ["==", ["get", "layer"], layer],
          paint: {
            "circle-radius": 5,
            "circle-color": legendItems[index]?.colour ?? PALETTE[index % PALETTE.length],
            "circle-stroke-color": CREAM,
            "circle-stroke-width": 1,
          },
        });
      } else {
        this.map.setLayoutProperty(id, "visibility", "visible");
      }

      if (!this.amenityLayersBound.has(layer)) {
        this.bindAmenityInteractions(layer, id);
        this.amenityLayersBound.add(layer);
      }
    });

    this.activeAmenityLayers = desiredLayers;
    this.renderLegend();
  }

  setSelectedPlaceId(placeId: string | null): void {
    this.selectedPlaceId = placeId;
    if (!this.choroplethInitialised) return;
    this.map.setFilter(CHOROPLETH_SELECTED, [
      "==",
      ["get", PLACE_ID_KEY],
      placeId ?? "__soundings_no_selection__",
    ]);
  }

  clearSelection(): void {
    this.setSelectedPlaceId(null);
  }

  resize(): void {
    this.map.resize();
  }

  destroy(): void {
    this.hoverPopup.remove();
    this.amenityHoverPopup.remove();
    this.amenityClickPopup.remove();
    this.legend.remove();
    this.map.remove();
  }

  private bindAreaInteractions(): void {
    this.map.on("mousemove", CHOROPLETH_FILL, (event) => {
      const feature = this.map.queryRenderedFeatures(event.point, {
        layers: [CHOROPLETH_FILL],
      })[0];
      if (!feature) return;

      const properties = (feature.properties ?? {}) as Record<string, unknown>;
      const name =
        (properties.name as string | undefined) ??
        (properties.place_name as string | undefined) ??
        (properties[PLACE_ID_KEY] as string | undefined) ??
        "—";
      const rawValue = properties[this.currentValueKey];
      const displayValue =
        typeof rawValue === "number"
          ? rawValue.toLocaleString("en-GB")
          : String(rawValue ?? "—");

      this.hoverPopup
        .setLngLat(event.lngLat)
        .setHTML(
          `<div style="font-family:system-ui,sans-serif"><strong>${escapeHtml(name)}</strong><br/>${escapeHtml(this.currentLabel)}: ${escapeHtml(displayValue)}</div>`,
        )
        .addTo(this.map);
      this.map.getCanvas().style.cursor = "pointer";
    });

    this.map.on("mouseleave", CHOROPLETH_FILL, () => {
      this.hoverPopup.remove();
      this.map.getCanvas().style.cursor = "";
    });

    this.map.on("click", CHOROPLETH_FILL, (event) => {
      const feature = this.map.queryRenderedFeatures(event.point, {
        layers: [CHOROPLETH_FILL],
      })[0];
      if (!feature) return;
      const properties = (feature.properties ?? {}) as Record<string, unknown>;
      const placeId = properties[PLACE_ID_KEY] as string | undefined;
      const name =
        (properties.name as string | undefined) ??
        (properties.place_name as string | undefined) ??
        placeId ??
        "—";
      const value = properties[this.currentValueKey];

      this.setSelectedPlaceId(placeId ?? null);
      this.onSelectArea?.({ placeId, name, value });
    });
  }

  private bindAmenityInteractions(layer: string, id: string): void {
    this.map.on("mouseenter", id, (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const properties = (feature.properties ?? {}) as Record<string, unknown>;
      const name =
        (properties.name as string | undefined) ?? amenityLayerLabel(layer);
      const coordinates = (feature.geometry as GeoJSON.Point).coordinates;
      this.amenityHoverPopup
        .setLngLat(coordinates as [number, number])
        .setHTML(
          `<div style="font-family:system-ui,sans-serif"><strong>${escapeHtml(name)}</strong><br/>${escapeHtml(amenityLayerLabel(layer))}</div>`,
        )
        .addTo(this.map);
      this.map.getCanvas().style.cursor = "pointer";
    });

    this.map.on("mouseleave", id, () => {
      this.amenityHoverPopup.remove();
      this.map.getCanvas().style.cursor = "";
    });

    this.map.on("click", id, (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const properties = (feature.properties ?? {}) as Record<string, unknown>;
      const name =
        (properties.name as string | undefined) ?? amenityLayerLabel(layer);
      const address =
        (properties.address as string | undefined) ??
        (properties.postcode as string | undefined);
      const coordinates = (feature.geometry as GeoJSON.Point).coordinates;
      this.amenityClickPopup
        .setLngLat(coordinates as [number, number])
        .setHTML(
          amenityPopupHtml({
            name,
            type: amenityLayerLabel(layer),
            address,
          }),
        )
        .addTo(this.map);
    });
  }

  private renderLegend(): void {
    const amenityItems = amenityLegendItems(this.activeAmenityLayers);
    const amenityHtml = amenityItems
      .map(
        (item) =>
          `<span class="legend-item"><span class="legend-swatch" style="background:${escapeHtml(item.colour)}"></span>${escapeHtml(item.label)}</span>`,
      )
      .join("");

    this.legend.innerHTML = this.choroplethLegendHtml + amenityHtml;
    if (this.legend.childNodes.length > 0) {
      if (!this.legend.parentElement) this.container.appendChild(this.legend);
    } else {
      this.legend.remove();
    }
  }
}
