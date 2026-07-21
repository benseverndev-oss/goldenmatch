import { describe, it, expect } from "vitest";
import { parse as parseYaml } from "yaml";
import { parseConfig, parseConfigYaml } from "../../src/core/index.js";

describe("parseConfig", () => {
  it("accepts snake_case keys", () => {
    const raw = {
      match_settings: [
        {
          name: "email_mk",
          type: "exact",
          fields: [{ field: "email", transforms: ["lowercase"], scorer: "exact", weight: 1.0 }],
        },
      ],
      threshold: 0.9,
    };
    const config = parseConfig(raw);
    expect(config.matchkeys?.length).toBe(1);
    expect(config.matchkeys?.[0]?.name).toBe("email_mk");
    expect(config.threshold).toBe(0.9);
  });

  it("accepts camelCase keys", () => {
    const raw = {
      matchkeys: [
        {
          name: "mk1",
          type: "weighted",
          fields: [{ field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 }],
          threshold: 0.85,
        },
      ],
    };
    const config = parseConfig(raw);
    const mk0 = config.matchkeys?.[0];
    expect(mk0?.type).toBe("weighted");
    if (mk0?.type === "weighted") {
      expect(mk0.threshold).toBe(0.85);
    }
  });

  it("parses matchkeys fields array", () => {
    const raw = {
      matchkeys: [
        {
          name: "m",
          type: "weighted",
          fields: [
            { field: "first", transforms: ["lowercase"], scorer: "jaro_winkler", weight: 0.5 },
            { field: "last", transforms: [], scorer: "jaro_winkler", weight: 1.0 },
          ],
        },
      ],
    };
    const config = parseConfig(raw);
    expect(config.matchkeys?.[0]?.fields.length).toBe(2);
    expect(config.matchkeys?.[0]?.fields[0]?.weight).toBe(0.5);
  });

  it("parses blocking config", () => {
    const raw = {
      blocking: {
        strategy: "static",
        keys: [{ fields: ["zip"], transforms: ["lowercase"] }],
        max_block_size: 1000,
        skip_oversized: true,
      },
    };
    const config = parseConfig(raw);
    expect(config.blocking?.strategy).toBe("static");
    expect(config.blocking?.maxBlockSize).toBe(1000);
    expect(config.blocking?.skipOversized).toBe(true);
    expect(config.blocking?.keys.length).toBe(1);
  });

  it("parses snake_case field_transforms with verbatim field-name keys (#1832)", () => {
    // A Python-written config carries `field_transforms` keyed by field names
    // (some snake_case). The outer key camelizes to `fieldTransforms`, but the
    // INNER field-name keys must stay verbatim so they match the `fields` array
    // and buildBlockKey's per-field lookup resolves.
    const raw = {
      blocking: {
        strategy: "static",
        keys: [
          {
            fields: ["surname", "first_name", "dob"],
            transforms: [],
            field_transforms: { dob: ["substring:0:4"], first_name: ["lowercase"] },
          },
        ],
      },
    };
    const config = parseConfig(raw);
    const key = config.blocking?.keys[0];
    expect(key?.fields).toEqual(["surname", "first_name", "dob"]);
    expect(key?.fieldTransforms).toEqual({
      dob: ["substring:0:4"],
      first_name: ["lowercase"],
    });
  });

  it("normalizes golden_rules.default -> defaultStrategy", () => {
    const raw = {
      golden_rules: {
        default: "most_complete",
      },
    };
    const config = parseConfig(raw);
    expect(config.goldenRules?.defaultStrategy).toBe("most_complete");
  });

  it("accepts goldenRules.defaultStrategy directly", () => {
    const raw = {
      goldenRules: {
        defaultStrategy: "majority_vote",
      },
    };
    const config = parseConfig(raw);
    expect(config.goldenRules?.defaultStrategy).toBe("majority_vote");
  });

  it("throws on invalid config (not an object)", () => {
    expect(() => parseConfig("not-an-object")).toThrow();
    expect(() => parseConfig(null)).toThrow();
  });

  it("throws on invalid nested config (matchkey without name)", () => {
    const raw = {
      matchkeys: [{ type: "exact", fields: [] }],
    };
    expect(() => parseConfig(raw)).toThrow();
  });

  // -------------------------------------------------------------------------
  // String-union validation
  // -------------------------------------------------------------------------

  describe("string-union validation", () => {
    it("throws on invalid matchkey type with clear message listing valid options", () => {
      const raw = {
        matchkeys: [
          {
            name: "bad",
            type: "garbage",
            fields: [{ field: "x", transforms: [], scorer: "exact", weight: 1 }],
          },
        ],
      };
      try {
        parseConfig(raw);
        throw new Error("should have thrown");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        expect(msg).toContain("garbage");
        expect(msg).toContain("exact");
        expect(msg).toContain("weighted");
        expect(msg).toContain("probabilistic");
      }
    });

    it("throws on invalid transform with valid options listed", () => {
      const raw = {
        matchkeys: [
          {
            name: "mk",
            type: "weighted",
            fields: [
              {
                field: "name",
                transforms: ["not_a_real_transform"],
                scorer: "jaro_winkler",
                weight: 1,
              },
            ],
          },
        ],
      };
      try {
        parseConfig(raw);
        throw new Error("should have thrown");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        expect(msg).toContain("not_a_real_transform");
        expect(msg).toContain("lowercase");
        expect(msg).toContain("substring");
        expect(msg).toContain("qgram");
      }
    });

    it("throws on invalid blocking strategy", () => {
      const raw = {
        blocking: {
          strategy: "nonsense",
          keys: [{ fields: ["zip"], transforms: [] }],
        },
      };
      expect(() => parseConfig(raw)).toThrow(/nonsense/);
    });

    it("throws on invalid golden_rules field strategy", () => {
      const raw = {
        golden_rules: {
          default: "most_complete",
          field_rules: {
            email: { strategy: "pick_worst" },
          },
        },
      };
      expect(() => parseConfig(raw)).toThrow(/pick_worst/);
    });

    it("throws on invalid standardizer", () => {
      const raw = {
        standardization: {
          rules: {
            name: ["scramble"],
          },
        },
      };
      expect(() => parseConfig(raw)).toThrow(/scramble/);
    });

    it("throws on invalid memory backend", () => {
      const raw = {
        memory: {
          enabled: true,
          backend: "redis",
        },
      };
      expect(() => parseConfig(raw)).toThrow(/redis/);
    });

    it("accepts parametric transforms (substring, qgram, bloom_filter)", () => {
      const raw = {
        matchkeys: [
          {
            name: "mk",
            type: "weighted",
            fields: [
              {
                field: "a",
                transforms: ["substring:0:3", "qgram:3", "bloom_filter"],
                scorer: "jaro_winkler",
                weight: 1,
              },
              {
                field: "b",
                transforms: ["bloom_filter:high"],
                scorer: "dice",
                weight: 1,
              },
            ],
          },
        ],
      };
      const config = parseConfig(raw);
      expect(config.matchkeys?.[0]?.fields[0]?.transforms).toEqual([
        "substring:0:3",
        "qgram:3",
        "bloom_filter",
      ]);
      expect(config.matchkeys?.[0]?.fields[1]?.transforms).toEqual([
        "bloom_filter:high",
      ]);
    });

    it("unknown scorer only warns (does not throw)", () => {
      const warnings: string[] = [];
      const origWarn = console.warn;
      console.warn = (msg: unknown) => {
        warnings.push(String(msg));
      };
      try {
        const raw = {
          matchkeys: [
            {
              name: "mk",
              type: "weighted",
              fields: [
                {
                  field: "a",
                  transforms: ["lowercase"],
                  scorer: "my_plugin_scorer",
                  weight: 1,
                },
              ],
            },
          ],
        };
        const config = parseConfig(raw);
        expect(config.matchkeys?.[0]?.fields[0]?.scorer).toBe("my_plugin_scorer");
        expect(warnings.join(" ")).toContain("my_plugin_scorer");
      } finally {
        console.warn = origWarn;
      }
    });

    it("accepts valid known scorers without warning", () => {
      const warnings: string[] = [];
      const origWarn = console.warn;
      console.warn = (msg: unknown) => {
        warnings.push(String(msg));
      };
      try {
        const raw = {
          matchkeys: [
            {
              name: "mk",
              type: "weighted",
              fields: [
                {
                  field: "a",
                  transforms: ["lowercase"],
                  scorer: "jaro_winkler",
                  weight: 1,
                },
              ],
            },
          ],
        };
        parseConfig(raw);
        expect(warnings).toHaveLength(0);
      } finally {
        console.warn = origWarn;
      }
    });
  });

  // -------------------------------------------------------------------------
  // negative_evidence parsing (was silently dropped for all three types)
  // -------------------------------------------------------------------------

  describe("negative_evidence parsing", () => {
    const neSnake = [
      {
        field: "phone",
        transforms: ["digits_only"],
        scorer: "exact",
        threshold: 0.5,
        penalty: 0.4,
      },
    ];

    it("parses negative_evidence on weighted matchkeys (regression: was dropped)", () => {
      const raw = {
        matchkeys: [
          {
            name: "w",
            type: "weighted",
            threshold: 0.9,
            fields: [
              { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1 },
            ],
            negative_evidence: neSnake,
          },
        ],
      };
      const config = parseConfig(raw);
      const mk = config.matchkeys?.[0];
      expect(mk?.type).toBe("weighted");
      const ne = mk?.negativeEvidence;
      expect(ne?.length).toBe(1);
      expect(ne?.[0]?.field).toBe("phone");
      expect(ne?.[0]?.transforms).toEqual(["digits_only"]);
      expect(ne?.[0]?.scorer).toBe("exact");
      expect(ne?.[0]?.threshold).toBe(0.5);
      expect(ne?.[0]?.penalty).toBe(0.4);
    });

    it("parses negative_evidence on exact matchkeys", () => {
      const raw = {
        matchkeys: [
          {
            name: "e",
            type: "exact",
            fields: [{ field: "email", transforms: ["lowercase"], scorer: "exact", weight: 1 }],
            negative_evidence: neSnake,
          },
        ],
      };
      const config = parseConfig(raw);
      const mk = config.matchkeys?.[0];
      expect(mk?.type).toBe("exact");
      const ne = mk?.negativeEvidence;
      expect(ne?.length).toBe(1);
      expect(ne?.[0]?.field).toBe("phone");
      expect(ne?.[0]?.penalty).toBe(0.4);
    });

    it("parses negative_evidence on probabilistic matchkeys, camelizing penalty_bits", () => {
      const raw = {
        matchkeys: [
          {
            name: "p",
            type: "probabilistic",
            fields: [
              { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1 },
            ],
            negative_evidence: [
              {
                field: "phone",
                transforms: ["digits_only"],
                scorer: "exact",
                threshold: 0.5,
                penalty_bits: 2.5,
              },
            ],
          },
        ],
      };
      const config = parseConfig(raw);
      const mk = config.matchkeys?.[0];
      expect(mk?.type).toBe("probabilistic");
      const ne = mk?.negativeEvidence;
      expect(ne?.length).toBe(1);
      expect(ne?.[0]?.field).toBe("phone");
      // penalty omitted on the EM-learned/penalty_bits probabilistic shape.
      expect(ne?.[0]?.penalty).toBeUndefined();
      // penalty_bits camelized, carried through, and typed.
      expect(ne?.[0]?.penaltyBits).toBe(2.5);
    });

    it("parses negative_evidence from a YAML config (fan-out lever ingestion path)", () => {
      const yamlStr = [
        "matchkeys:",
        "  - name: w",
        "    type: weighted",
        "    threshold: 0.9",
        "    fields:",
        "      - field: name",
        "        transforms: [lowercase]",
        "        scorer: jaro_winkler",
        "        weight: 1.0",
        "    negative_evidence:",
        "      - field: phone",
        "        transforms: [digits_only]",
        "        scorer: exact",
        "        threshold: 0.5",
        "        penalty: 0.4",
      ].join("\n");
      const config = parseConfigYaml(yamlStr, parseYaml);
      const ne = config.matchkeys?.[0]?.negativeEvidence;
      expect(ne?.length).toBe(1);
      expect(ne?.[0]?.field).toBe("phone");
      expect(ne?.[0]?.transforms).toEqual(["digits_only"]);
      expect(ne?.[0]?.penalty).toBe(0.4);
    });
  });

  // -------------------------------------------------------------------------
  // negative_evidence validation matrix (mirrors Python config/schemas.py:
  // per-matchkey-type penalty vs penalty_bits rules + range checks)
  // -------------------------------------------------------------------------

  describe("negative_evidence validation matrix", () => {
    const mkWith = (
      type: "exact" | "weighted" | "probabilistic",
      ne: Record<string, unknown>,
    ) => ({
      matchkeys: [
        {
          name: "mk",
          type,
          ...(type === "weighted" ? { threshold: 0.9 } : {}),
          fields: [
            { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1 },
          ],
          negative_evidence: [
            {
              field: "phone",
              transforms: ["digits_only"],
              scorer: "exact",
              threshold: 0.5,
              ...ne,
            },
          ],
        },
      ],
    });

    it("weighted NE without penalty throws naming penalty", () => {
      expect(() => parseConfig(mkWith("weighted", {}))).toThrow(
        /requires 'penalty'/,
      );
    });

    it("exact NE without penalty throws naming penalty", () => {
      expect(() => parseConfig(mkWith("exact", {}))).toThrow(
        /requires 'penalty'/,
      );
    });

    it("weighted NE with penalty_bits throws (probabilistic-only knob)", () => {
      expect(() =>
        parseConfig(mkWith("weighted", { penalty: 0.4, penalty_bits: 2.5 })),
      ).toThrow(/penalty_bits.*only valid on\s+probabilistic/s);
    });

    it("exact NE with penalty_bits throws (probabilistic-only knob)", () => {
      expect(() =>
        parseConfig(mkWith("exact", { penalty: 0.4, penalty_bits: 2.5 })),
      ).toThrow(/penalty_bits.*only valid on\s+probabilistic/s);
    });

    it("probabilistic NE with penalty throws, pointing at penalty_bits", () => {
      // /EM-learned/ is unique to the probabilistic-side message (the
      // weighted-side message also mentions penalty_bits, so matching that
      // alone would pass under a matrix routing bug).
      expect(() =>
        parseConfig(mkWith("probabilistic", { penalty: 0.4 })),
      ).toThrow(/EM-learned/);
    });

    it("probabilistic NE with penalty_bits parses, penaltyBits typed and set", () => {
      const config = parseConfig(mkWith("probabilistic", { penalty_bits: 2.5 }));
      const ne = config.matchkeys?.[0]?.negativeEvidence;
      expect(ne?.[0]?.penaltyBits).toBe(2.5);
      expect(ne?.[0]?.penalty).toBeUndefined();
    });

    it("probabilistic NE with neither penalty nor penalty_bits parses (EM-learned shape)", () => {
      const config = parseConfig(mkWith("probabilistic", {}));
      const ne = config.matchkeys?.[0]?.negativeEvidence;
      expect(ne?.length).toBe(1);
      expect(ne?.[0]?.penalty).toBeUndefined();
      expect(ne?.[0]?.penaltyBits).toBeUndefined();
    });

    it("derive_from NE on a weighted matchkey throws (not supported in goldenmatch-js)", () => {
      expect(() =>
        parseConfig(
          mkWith("weighted", {
            penalty: 0.4,
            derive_from: ["first_name", "last_name"],
          }),
        ),
      ).toThrow(/derive_from negative evidence is not supported in goldenmatch-js/);
    });

    it("derive_from NE on a probabilistic matchkey throws (not supported in goldenmatch-js)", () => {
      expect(() =>
        parseConfig(
          mkWith("probabilistic", {
            derive_from: ["first_name", "last_name"],
          }),
        ),
      ).toThrow(/derive_from negative evidence is not supported in goldenmatch-js/);
    });

    it("NE threshold outside [0, 1] throws", () => {
      expect(() =>
        parseConfig(mkWith("weighted", { penalty: 0.4, threshold: 1.5 })),
      ).toThrow(/threshold/);
      expect(() =>
        parseConfig(mkWith("weighted", { penalty: 0.4, threshold: -0.1 })),
      ).toThrow(/threshold/);
    });

    it("NE penalty outside [0, 1] throws", () => {
      expect(() => parseConfig(mkWith("weighted", { penalty: 1.5 }))).toThrow(
        /penalty/,
      );
      expect(() => parseConfig(mkWith("weighted", { penalty: -0.1 }))).toThrow(
        /penalty/,
      );
    });

    it("NE threshold NaN throws (negated-conjunction range check rejects NaN)", () => {
      // YAML can't express NaN, but parseConfig takes JS objects directly.
      expect(() =>
        parseConfig(mkWith("weighted", { penalty: 0.4, threshold: NaN })),
      ).toThrow(/threshold/);
    });

    it("mistyped NE penalty (string) throws a type error naming penalty", () => {
      // Easy YAML mistake: quoted number. Must NOT vanish into undefined
      // (misleading "requires 'penalty'" on weighted, silent accept on
      // probabilistic — Python's Pydantic raises a type error here).
      expect(() =>
        parseConfig(mkWith("weighted", { penalty: "0.4" })),
      ).toThrow(/penalty.*expected number, got string/);
    });

    it("mistyped NE penalty_bits (string) throws a type error on probabilistic", () => {
      expect(() =>
        parseConfig(mkWith("probabilistic", { penalty_bits: "2.5" })),
      ).toThrow(/penalty_bits.*expected number, got string/);
    });

    it("NE penalty_bits is unconstrained (negative and large values parse)", () => {
      const neg = parseConfig(mkWith("probabilistic", { penalty_bits: -12.5 }));
      expect(neg.matchkeys?.[0]?.negativeEvidence?.[0]?.penaltyBits).toBe(-12.5);
      const big = parseConfig(mkWith("probabilistic", { penalty_bits: 900 }));
      expect(big.matchkeys?.[0]?.negativeEvidence?.[0]?.penaltyBits).toBe(900);
    });

    it("exact matchkey carries threshold through the loader (regression: was dropped)", () => {
      const raw = {
        matchkeys: [
          {
            name: "e",
            type: "exact",
            threshold: 0.6,
            fields: [
              { field: "email", transforms: ["lowercase"], scorer: "exact", weight: 1 },
            ],
            negative_evidence: [
              {
                field: "phone",
                transforms: ["digits_only"],
                scorer: "exact",
                threshold: 0.5,
                penalty: 0.4,
              },
            ],
          },
        ],
      };
      const config = parseConfig(raw);
      const mk = config.matchkeys?.[0];
      expect(mk?.type).toBe("exact");
      if (mk?.type === "exact") {
        expect(mk.threshold).toBe(0.6);
      }
    });
  });
});
