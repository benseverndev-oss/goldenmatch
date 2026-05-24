/**
 * Tests for the `carceral` domain pack.
 * Mirrors packages/python/goldenflow/tests/domains/test_carceral.py.
 */

import { describe, it, expect } from "vitest";
import {
  PACK,
  CARCERAL_OPERATOR_ORGS,
  CARCERAL_BOP_ABBREVIATIONS,
  CARCERAL_STATE_COMPLEX_ALIASES,
} from "../../src/core/domains/carceral.js";
import { getTransform } from "../../src/core/transforms/registry.js";
import { loadDomain, listDomains } from "../../src/core/domains/index.js";
import type { ColumnValue, Row } from "../../src/core/types.js";

// Importing carceral.js registers the transforms as a side effect.
function runSeries(name: string, values: ColumnValue[]): ColumnValue[] {
  const info = getTransform(name);
  expect(info).toBeDefined();
  return info!.func(values) as ColumnValue[];
}

function runDataframe(name: string, rows: Row[]): Row[] {
  const info = getTransform(name);
  expect(info).toBeDefined();
  return info!.func(rows, "name") as Row[];
}

// --- Metadata ---

describe("carceral pack metadata", () => {
  it("has the expected name and transforms", () => {
    expect(PACK.name).toBe("carceral");
    expect(PACK.transforms).toContain("carceral_org_strip");
    expect(PACK.transforms).toContain("carceral_abbreviate");
    expect(PACK.transforms).toContain("carceral_name_normalize");
    expect(PACK.transforms).toContain("latlng_pack");
    // Composes with existing transforms
    expect(PACK.transforms).toContain("address_standardize");
    expect(PACK.transforms).toContain("zip_normalize");
  });

  it("constants contain the expected members", () => {
    expect(CARCERAL_OPERATOR_ORGS.has("TDCJ")).toBe(true);
    expect(CARCERAL_OPERATOR_ORGS.has("MDOC")).toBe(true);
    expect(CARCERAL_OPERATOR_ORGS.has("GEO")).toBe(true);
    expect("USP" in CARCERAL_BOP_ABBREVIATIONS).toBe(true);
    expect("ASPC" in CARCERAL_STATE_COMPLEX_ALIASES).toBe(true);
  });

  it("is loadable via loadDomain and listed", async () => {
    expect(listDomains()).toContain("carceral");
    const loaded = await loadDomain("carceral");
    expect(loaded?.name).toBe("carceral");
  });
});

// --- carceral_org_strip ---

describe("carceral_org_strip", () => {
  it("strips comma-separated operator prefix", () => {
    const out = runSeries("carceral_org_strip", [
      "MDOC, SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION",
      "TDCJ, ALLRED UNIT",
    ]);
    expect(out[0]).toBe("SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION");
    expect(out[1]).toBe("ALLRED UNIT");
  });

  it("strips hyphen-separated operator prefix", () => {
    const out = runSeries("carceral_org_strip", ["TDCJ - DARRINGTON WWTP", "GEO - LAKEVIEW"]);
    expect(out[0]).toBe("DARRINGTON WWTP");
    expect(out[1]).toBe("LAKEVIEW");
  });

  it("strips phrase-form operator prefix", () => {
    const out = runSeries("carceral_org_strip", [
      "TX DEPT OF CRIM JUST- MCCONNELL UNIT",
      "PA DEPT OF CORR/CHESTER SCI",
      "TX DEPT OF CRIMINAL JUSTICE - ALLRED UNIT",
    ]);
    expect(out[0]).toBe("MCCONNELL UNIT");
    expect(out[1]).toBe("CHESTER SCI");
    expect(out[2]).toBe("ALLRED UNIT");
  });

  it("preserves names with no prefix", () => {
    const out = runSeries("carceral_org_strip", ["ALLEGHENY COUNTY JAIL", "CALHOUN COUNTY JAIL"]);
    expect(out[0]).toBe("ALLEGHENY COUNTY JAIL");
    expect(out[1]).toBe("CALHOUN COUNTY JAIL");
  });

  it("preserves mid-string acronyms", () => {
    const out = runSeries("carceral_org_strip", [
      "NORTH END TRANSITIONAL HOUSING UNIT / WORK CENTER",
    ]);
    expect(out[0]).toBe("NORTH END TRANSITIONAL HOUSING UNIT / WORK CENTER");
  });

  it("passes null/empty through", () => {
    const out = runSeries("carceral_org_strip", [null, ""]);
    expect(out[0]).toBe(null);
    expect(out[1]).toBe("");
  });
});

// --- carceral_abbreviate ---

describe("carceral_abbreviate", () => {
  it("expands BOP facility types", () => {
    const out = runSeries("carceral_abbreviate", [
      "USP HAZELTON",
      "FCI DUBLIN",
      "FCC YAZOO CITY",
      "FPC PENSACOLA",
      "FMC SPRINGFIELD",
    ]);
    expect(out[0]).toBe("UNITED STATES PENITENTIARY HAZELTON");
    expect(out[1]).toBe("FEDERAL CORRECTIONAL INSTITUTION DUBLIN");
    expect(out[2]).toBe("FEDERAL CORRECTIONAL COMPLEX YAZOO CITY");
    expect(out[3]).toBe("FEDERAL PRISON CAMP PENSACOLA");
    expect(out[4]).toBe("FEDERAL MEDICAL CENTER SPRINGFIELD");
  });

  it("expands state-complex aliases", () => {
    const out = runSeries("carceral_abbreviate", ["ASPC-LEWIS", "ASP - YUMA COMPLEX", "APS-PERRYVILLE"]);
    expect(String(out[0])).toContain("ARIZONA STATE PRISON COMPLEX");
    expect(String(out[1])).toContain("ARIZONA STATE PRISON");
    expect(String(out[2])).toContain("ARIZONA STATE PRISON");
  });

  it("is word-bounded (no mid-token expansion)", () => {
    const out = runSeries("carceral_abbreviate", ["NUSPACE", "CALLUSP"]);
    expect(out[0]).toBe("NUSPACE");
    expect(out[1]).toBe("CALLUSP");
  });
});

// --- carceral_name_normalize (composite) ---

describe("carceral_name_normalize", () => {
  it("collapses HIFLD-form and ECHO-form to the same string", () => {
    const out = runSeries("carceral_name_normalize", [
      "USP HAZELTON",
      "UNITED STATES PENITENTIARY HAZELTON",
      "FDC HOUSTON",
      "FEDERAL DETENTION CENTER HOUSTON",
    ]);
    expect(out[0]).toBe("UNITED STATES PENITENTIARY HAZELTON");
    expect(out[1]).toBe("UNITED STATES PENITENTIARY HAZELTON");
    expect(out[2]).toBe("FEDERAL DETENTION CENTER HOUSTON");
    expect(out[3]).toBe("FEDERAL DETENTION CENTER HOUSTON");
  });

  it("strips punctuation", () => {
    const out = runSeries("carceral_name_normalize", ["LT. SHERMAN WALKER CORRECTIONAL FACILITY"]);
    expect(out[0]).toBe("LT SHERMAN WALKER CORRECTIONAL FACILITY");
  });

  it("handles operator prefix plus abbreviation", () => {
    const out = runSeries("carceral_name_normalize", [
      "MDOC, SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION",
      "SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION",
    ]);
    expect(out[0]).toBe(out[1]);
    expect(out[0]).toBe("SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION");
  });

  it("collapses the Arizona ASPC/APS pattern to a common prefix", () => {
    const out = runSeries("carceral_name_normalize", ["ASPC-LEWIS", "APS-LEWIS COMPLEX"]);
    expect(String(out[0])).toContain("ARIZONA STATE PRISON");
    expect(String(out[1])).toContain("ARIZONA STATE PRISON");
    expect(String(out[0])).toContain("LEWIS");
    expect(String(out[1])).toContain("LEWIS");
  });
});

// --- latlng_pack ---

describe("latlng_pack", () => {
  it("packs when both present (JS number stringification)", () => {
    const out = runDataframe("latlng_pack", [
      { id: "a", lat: 39, lng: -90 },
      { id: "b", lat: 32.5, lng: -116.5 },
    ]);
    expect(out[0]!["latlng"]).toBe("39|-90");
    expect(out[1]!["latlng"]).toBe("32.5|-116.5");
  });

  it("emits empty string when either coordinate is null", () => {
    const out = runDataframe("latlng_pack", [
      { id: "a", lat: 39, lng: null },
      { id: "b", lat: null, lng: -90 },
      { id: "c", lat: 32.5, lng: -116.5 },
    ]);
    expect(out[0]!["latlng"]).toBe("");
    expect(out[1]!["latlng"]).toBe("");
    expect(out[2]!["latlng"]).toBe("32.5|-116.5");
  });

  it("is a no-op when lat/lng columns are missing", () => {
    const rows: Row[] = [
      { id: "a", name: "x" },
      { id: "b", name: "y" },
    ];
    const out = runDataframe("latlng_pack", rows);
    expect("latlng" in out[0]!).toBe(false);
    expect(out.length).toBe(rows.length);
  });
});
