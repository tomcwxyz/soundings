import { describe, expect, it } from "vitest";
import { prepareChoroplethFeatureCollection } from "../interactive-map";

function collection(values: Array<{ id: string; value: number | null }>): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: values.map(({ id, value }, index) => ({
      type: "Feature",
      geometry: {
        type: "Polygon",
        coordinates: [[
          [index, 0],
          [index + 0.5, 0],
          [index + 0.5, 0.5],
          [index, 0],
        ]],
      },
      properties: { id, value },
    })),
  };
}

describe("prepareChoroplethFeatureCollection", () => {
  it("adds stable place ids and rank values without mutating the source collection", () => {
    const source = collection([
      { id: "lsoa21:A", value: 10 },
      { id: "lsoa21:B", value: 30 },
      { id: "lsoa21:C", value: 20 },
    ]);

    const prepared = prepareChoroplethFeatureCollection(
      source,
      "value",
      "population.total",
    );

    expect(prepared).not.toBe(source);
    expect(prepared.features.map((feature) => feature.properties?.__place_id)).toEqual([
      "lsoa21:A",
      "lsoa21:B",
      "lsoa21:C",
    ]);
    expect(prepared.features.map((feature) => feature.properties?.__rank)).toEqual([
      0,
      1,
      0.5,
    ]);
    expect(source.features[0]?.properties).not.toHaveProperty("__rank");
  });

  it("reverses visual rank for PPFI deciles", () => {
    const source = collection([
      { id: "lsoa21:A", value: 1 },
      { id: "lsoa21:B", value: 10 },
    ]);

    const prepared = prepareChoroplethFeatureCollection(
      source,
      "value",
      "food.ppfi.overall_decile",
    );

    expect(prepared.features.map((feature) => feature.properties?.__rank)).toEqual([
      1,
      0,
    ]);
  });

  it("leaves missing values without a rank", () => {
    const source = collection([
      { id: "lsoa21:A", value: null },
      { id: "lsoa21:B", value: 4 },
    ]);

    const prepared = prepareChoroplethFeatureCollection(
      source,
      "value",
      "population.total",
    );

    expect(prepared.features[0]?.properties).not.toHaveProperty("__rank");
    expect(prepared.features[1]?.properties?.__rank).toBe(0.5);
  });
});
