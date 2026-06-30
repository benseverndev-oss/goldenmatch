import { defineConfig, markdown } from "sourcey";

export default defineConfig({
  title: "GoldenMatch Docs",
  output: "./site",
  navigation: {
    tabs: [
      {
        tab: "Documentation",
        slug: "",
        source: markdown({
          groups: [
            {
              group: "Guides",
              pages: ["docs/ci-lanes.md", "docs/columnar-pipeline-wiring.md", "docs/distributed-ray-cluster-setup.md", "docs/distributed-ray-roadmap.md", "docs/distributed-sail-cluster-setup.md", "docs/duckdb-sql-scoring-research.md", "docs/er-vendor-comparison.md", "docs/explicit-config.md", "docs/future-work.md", "docs/org-transfer-2026-05-15.md", "docs/oss-llm-usage.md", "docs/quality-invariant-scale.md", "docs/reproducing-benchmarks.md", "docs/scale-audit-2026-05.md", "docs/scale-envelope.md", "docs/versioning-policy.md"],
            },
            {
              group: "Design Specs",
              pages: ["docs/superpowers/specs/2026-05-01-goldenmatch-monorepo-fold-in-design.md", "docs/superpowers/specs/2026-05-01-infermap-goldencheck-handoff-design.md", "docs/superpowers/specs/2026-05-02-performance-audit-checklist.md", "docs/superpowers/specs/2026-05-02-pnpm-turbo-migration.md", "docs/superpowers/specs/2026-05-04-hoist-matchkey-transforms.md", "docs/superpowers/specs/2026-05-04-learning-memory-completion.md", "docs/superpowers/specs/2026-05-05-ts-parity-learning-memory-design.md", "docs/superpowers/specs/2026-05-08-autoconfig-best-effort-commit-design.md", "docs/superpowers/specs/2026-05-08-autoconfig-indicators-design.md", "docs/superpowers/specs/2026-05-08-autoconfig-negative-evidence-and-clustered-identity-design.md", "docs/superpowers/specs/2026-05-08-competitive-strategy-review.md", "docs/superpowers/specs/2026-05-09-autoconfig-path-y-design.md", "docs/superpowers/specs/2026-05-10-ts-parity-arc-design.md", "docs/superpowers/specs/2026-05-13-golden-suite-package-audit.md", "docs/superpowers/specs/2026-05-13-goldenpipe-v1.2-identity-orchestration-design.md", "docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md", "docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md", "docs/superpowers/specs/2026-05-15-map-elements-attack-design.md", "docs/superpowers/specs/2026-05-15-map-elements-catalog.md", "docs/superpowers/specs/2026-05-15-post-controller-full-df-perf-design.md"],
            },
          ],
        }),
      },
    ],
  },
});
