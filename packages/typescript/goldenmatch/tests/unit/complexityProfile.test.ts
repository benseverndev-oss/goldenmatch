import { describe, it, expect } from "vitest";
import {
  HealthVerdict,
  StopReason,
  makeDataProfile,
  makeBlockingProfile,
  makeScoringProfile,
  makeClusterProfile,
  makeComplexityProfile,
  dataHealth,
  blockingHealth,
  scoringHealth,
  clusterHealth,
  complexityHealth,
  normalizedSignalVector,
  computeDataProfile,
} from "../../src/core/complexityProfile.js";

describe("complexityProfile — health rollups (Python parity)", () => {
  it("dataHealth: empty rows → RED", () => {
    expect(dataHealth(makeDataProfile())).toBe(HealthVerdict.RED);
  });

  it("dataHealth: single column → YELLOW", () => {
    expect(
      dataHealth(makeDataProfile({ nRows: 5, nCols: 1, columnTypes: { a: "text" } })),
    ).toBe(HealthVerdict.YELLOW);
  });

  it("dataHealth: uniform column types → YELLOW", () => {
    expect(
      dataHealth(
        makeDataProfile({
          nRows: 5,
          nCols: 2,
          columnTypes: { a: "text", b: "text" },
        }),
      ),
    ).toBe(HealthVerdict.YELLOW);
  });

  it("dataHealth: healthy mix → GREEN", () => {
    expect(
      dataHealth(
        makeDataProfile({
          nRows: 5,
          nCols: 2,
          columnTypes: { a: "text", b: "numeric" },
        }),
      ),
    ).toBe(HealthVerdict.GREEN);
  });

  it("blockingHealth: n_blocks=0 → RED", () => {
    expect(blockingHealth(makeBlockingProfile(), 100)).toBe(HealthVerdict.RED);
  });

  it("blockingHealth: skewed p99 → RED", () => {
    expect(
      blockingHealth(
        makeBlockingProfile({ nBlocks: 10, blockSizesP99: 1000, reductionRatio: 0.9 }),
        100,
      ),
    ).toBe(HealthVerdict.RED);
  });

  it("blockingHealth: low reduction → RED", () => {
    expect(
      blockingHealth(
        makeBlockingProfile({ nBlocks: 10, blockSizesP99: 10, reductionRatio: 0.2 }),
        100,
      ),
    ).toBe(HealthVerdict.RED);
  });

  it("blockingHealth: singletons dominate → YELLOW", () => {
    expect(
      blockingHealth(
        makeBlockingProfile({
          nBlocks: 10,
          blockSizesP99: 10,
          reductionRatio: 0.9,
          singletonBlockCount: 8,
        }),
        100,
      ),
    ).toBe(HealthVerdict.YELLOW);
  });

  it("blockingHealth: healthy → GREEN", () => {
    expect(
      blockingHealth(
        makeBlockingProfile({
          nBlocks: 10,
          blockSizesP99: 10,
          reductionRatio: 0.9,
          singletonBlockCount: 1,
        }),
        100,
      ),
    ).toBe(HealthVerdict.GREEN);
  });

  it("scoringHealth: nothing scored → RED", () => {
    expect(scoringHealth(makeScoringProfile())).toBe(HealthVerdict.RED);
  });

  it("scoringHealth: borderline-heavy → YELLOW", () => {
    expect(
      scoringHealth(
        makeScoringProfile({
          candidatesCompared: 100,
          nPairsScored: 50,
          massAboveThreshold: 0.6,
          massInBorderline: 0.5,
          dipStatistic: 0.05,
        }),
      ),
    ).toBe(HealthVerdict.YELLOW);
  });

  it("scoringHealth: healthy → GREEN", () => {
    expect(
      scoringHealth(
        makeScoringProfile({
          candidatesCompared: 100,
          nPairsScored: 50,
          massAboveThreshold: 0.6,
          massInBorderline: 0.1,
          dipStatistic: 0.05,
        }),
      ),
    ).toBe(HealthVerdict.GREEN);
  });

  it("clusterHealth: cluster eats >10% rows → RED", () => {
    expect(
      clusterHealth(makeClusterProfile({ clusterSizeMax: 50 }), 100),
    ).toBe(HealthVerdict.RED);
  });

  it("clusterHealth: low transitivity → RED", () => {
    expect(
      clusterHealth(
        makeClusterProfile({ transitivityRate: 0.5, clusterSizeMax: 2 }),
        100,
      ),
    ).toBe(HealthVerdict.RED);
  });

  it("complexityHealth: takes max severity across sub-profiles", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        nCols: 3,
        columnTypes: { a: "text", b: "numeric", c: "date" },
      }),
      blocking: makeBlockingProfile({
        nBlocks: 0,  // → RED
      }),
      scoring: makeScoringProfile({
        candidatesCompared: 100,
        massAboveThreshold: 0.6,
        dipStatistic: 0.05,
      }),
    });
    expect(complexityHealth(profile)).toBe(HealthVerdict.RED);
  });
});

describe("complexityProfile — normalizedSignalVector", () => {
  it("returns 8 signals clamped to [0, 1] where applicable", () => {
    const v = normalizedSignalVector(
      makeComplexityProfile({
        data: makeDataProfile({ nRows: 100 }),
        blocking: makeBlockingProfile({ reductionRatio: 0.9, blockSizesP99: 50 }),
        scoring: makeScoringProfile({
          dipStatistic: 0.05,
          massAboveThreshold: 0.7,
          massInBorderline: 0.1,
        }),
        cluster: makeClusterProfile({
          transitivityRate: 0.95,
          clusterSizeMax: 5,
          nClusters: 10,
        }),
      }),
    );
    expect(v).toHaveLength(8);
    for (const x of v) expect(x).toBeGreaterThanOrEqual(0);
    for (const x of v) expect(x).toBeLessThanOrEqual(1);
  });
});

describe("complexityProfile — StopReason enum", () => {
  it("includes all 8 stop reasons", () => {
    expect(Object.values(StopReason).sort()).toEqual(
      [
        "budget_iterations",
        "budget_time",
        "cancelled",
        "converged",
        "green",
        "oscillating",
        "policy_no_progress",
        "policy_satisfied",
      ].sort(),
    );
  });
});

describe("complexityProfile — computeDataProfile from rows", () => {
  it("counts user cols, classifies basic types", () => {
    const rows = [
      { name: "Alice", age: 30, when: new Date() },
      { name: "Bob", age: 25, when: new Date() },
      { name: "Carol", age: null, when: new Date() },
    ];
    const dp = computeDataProfile(rows);
    expect(dp.nRows).toBe(3);
    expect(dp.nCols).toBe(3);
    expect(dp.columnTypes["name"]).toBe("text");
    expect(dp.columnTypes["age"]).toBe("numeric");
    expect(dp.columnTypes["when"]).toBe("date");
    expect(dp.nullRate["age"]).toBeCloseTo(1 / 3, 4);
  });

  it("skips internal columns (prefix __)", () => {
    const rows = [{ name: "Alice", __row_id__: 0 }];
    const dp = computeDataProfile(rows);
    expect(dp.nCols).toBe(1);
    expect(Object.keys(dp.columnTypes)).toEqual(["name"]);
  });

  it("returns zero-row default for empty input", () => {
    const dp = computeDataProfile([]);
    expect(dp.nRows).toBe(0);
    expect(dp.nCols).toBe(0);
  });
});
