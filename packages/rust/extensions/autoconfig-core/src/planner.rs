//! Layer 1 — Planner: types, constants, `auto_chunk_size`, and `decide_plan`.
//!
//! Port oracle: `autoconfig_planner_rules.py` (8-rule registry, lines 444-453).
//! Rule registry order (first match wins):
//!   user_override → pathological → simple → fast_box → bucket_suggested
//!   → chunked → ray → duckdb
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum BackendName {
    PolarsDirect,
    Chunked,
    Duckdb,
    Ray,
    Bucket,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClusteringStrategy {
    InMemory,
    PartitionedUnionFind,
    StreamingCc,
}

// NO `None` variant: Python pair_spill_threshold is `Literal[...] | None`; 5 of 8
// rules leave it None -> JSON null. Model absence as Option (below), NOT a variant
// (a `None` variant under snake_case serializes "none" and breaks oracle parity).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SpillThreshold {
    Ram,
    Duckdb,
    DiskPerWorker,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct RuntimeProfile {
    pub available_ram_gb: f64,
    pub cpu_count: u32,
    pub disk_free_gb: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Capabilities {
    pub bucket_available: bool,
    pub ray_available: bool,
    pub ray_auto_select: bool,
    pub user_backend: Option<BackendName>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlannerInput {
    pub n_rows_full: u64,
    pub estimated_pair_count: u64,
    pub runtime: RuntimeProfile,
    pub caps: Capabilities,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ExecutionPlan {
    pub backend: BackendName,
    pub chunk_size: Option<u64>,
    pub max_workers: u32,
    pub pair_spill_threshold: Option<SpillThreshold>,
    pub clustering_strategy: ClusteringStrategy,
    pub rule_name: String,
}

// ── Named threshold constants (port from autoconfig_planner_rules.py) ────────
// Rule 1: pathological — predicate: n_rows <= 1 (no constant needed)

// Rule 2: simple plan
pub const SIMPLE_PLAN_MAX_ROWS: u64 = 100_000;
pub const SIMPLE_PLAN_MAX_PAIRS: u64 = 50_000_000;

// Rule 3: fast-box plan
pub const FAST_BOX_MIN_RAM_GB: f64 = 32.0;

// Rule 3b: bucket-suggested band
pub const BUCKET_SUGGESTED_MAX_ROWS: u64 = 750_000;
pub const PAIR_SCORE_BYTES: u64 = 64;
pub const BUCKET_RAM_SAFETY_FRACTION: f64 = 0.5;

// Rule 4: chunked plan
pub const CHUNKED_MAX_PAIRS: u64 = 5_000_000_000;
pub const CHUNKED_MIN_RAM_GB: f64 = 16.0;
pub const CHUNKED_TARGET_RAM_USE_FRACTION: f64 = 0.6;
pub const CHUNKED_BYTES_PER_ROW: u64 = 1024;

// Rule 6: Ray escape hatch
pub const RAY_MIN_ROWS: u64 = 50_000_000;

// Rule 5: DuckDB out-of-core
pub const DUCKDB_MIN_PAIRS: u64 = 5_000_000_000;
pub const DUCKDB_MAX_RAM_GB: f64 = 16.0;
pub const DUCKDB_MAX_WORKERS: u32 = 8;

// ── Helper ────────────────────────────────────────────────────────────────────

/// Port of `_scoring_backend()` from `autoconfig_planner_rules.py:27-41`.
///
/// The Python version probes the live Python environment; here the surface has
/// already resolved `native_enabled("block_scoring") && !GOLDENMATCH_PLANNER_BUCKET
/// opt-out` into `caps.bucket_available`.
fn scoring_backend(caps: &Capabilities) -> BackendName {
    if caps.bucket_available {
        BackendName::Bucket
    } else {
        BackendName::PolarsDirect
    }
}

// ── auto_chunk_size ───────────────────────────────────────────────────────────

/// Port of `auto_chunk_size` from `autoconfig_planner_rules.py:218-232`.
///
/// Target ~60 % of available RAM per chunk; clamp result to [10_000, 1_000_000].
pub fn auto_chunk_size(n_rows_full: u64, available_ram_gb: f64) -> u64 {
    let estimated_gb = (n_rows_full * CHUNKED_BYTES_PER_ROW) as f64 / (1024_f64.powi(3));
    let denom = (available_ram_gb * CHUNKED_TARGET_RAM_USE_FRACTION).max(0.001);
    let target_chunks = (estimated_gb / denom).ceil().max(1.0) as u64;
    let chunk = n_rows_full / target_chunks;
    chunk.clamp(10_000, 1_000_000)
}

// ── decide_plan ───────────────────────────────────────────────────────────────

/// Port of the 8-rule planner registry from `autoconfig_planner_rules.py:444-453`.
///
/// Rule order (first match wins):
/// `user_override` → `pathological` → `simple` → `fast_box` →
/// `bucket_suggested` → `chunked` → `ray` → `duckdb`
pub fn decide_plan(input: &PlannerInput) -> ExecutionPlan {
    let n = input.n_rows_full;
    let pairs = input.estimated_pair_count;
    let rt = &input.runtime;
    let caps = &input.caps;

    // Rule 7 (registry position 0): explicit user override — beats every other rule.
    // Python: `_user_set_backend` checks `context.get("user_backend") not in (None, "")`.
    if let Some(user_backend) = caps.user_backend {
        let chunk_size = if user_backend == BackendName::Chunked {
            Some(auto_chunk_size(n, rt.available_ram_gb))
        } else {
            None
        };
        return ExecutionPlan {
            backend: user_backend,
            chunk_size,
            max_workers: (16_u32).min(rt.cpu_count),
            pair_spill_threshold: None,
            clustering_strategy: ClusteringStrategy::InMemory,
            rule_name: "plan_user_override".into(),
        };
    }

    // Rule 1: pathological — n_rows <= 1.
    if n <= 1 {
        return ExecutionPlan {
            backend: BackendName::PolarsDirect,
            chunk_size: None,
            max_workers: 1,
            pair_spill_threshold: None,
            clustering_strategy: ClusteringStrategy::InMemory,
            rule_name: "plan_pathological".into(),
        };
    }

    // Rule 2: simple — n_rows < 100_000 AND pairs < 50_000_000.
    if n < SIMPLE_PLAN_MAX_ROWS && pairs < SIMPLE_PLAN_MAX_PAIRS {
        return ExecutionPlan {
            backend: scoring_backend(caps),
            chunk_size: None,
            max_workers: (4_u32).min(rt.cpu_count),
            pair_spill_threshold: None,
            clustering_strategy: ClusteringStrategy::InMemory,
            rule_name: "plan_selected_simple".into(),
        };
    }

    // Rule 3: fast_box — n_rows >= 100_000, pairs < 50_000_000, RAM >= 32 GB.
    if n >= SIMPLE_PLAN_MAX_ROWS && pairs < SIMPLE_PLAN_MAX_PAIRS && rt.available_ram_gb >= FAST_BOX_MIN_RAM_GB {
        return ExecutionPlan {
            backend: scoring_backend(caps),
            chunk_size: None,
            max_workers: (16_u32).min(rt.cpu_count),
            pair_spill_threshold: None,
            clustering_strategy: ClusteringStrategy::InMemory,
            rule_name: "plan_selected_fast_box".into(),
        };
    }

    // Rule 3b: bucket_suggested — sub-32 GB, 100k–750k rows, RAM-safe.
    // Python: `_is_bucket_suggested_eligible` (lines 171-186).
    let bucket_suggested_eligible = {
        let row_band = (SIMPLE_PLAN_MAX_ROWS..=BUCKET_SUGGESTED_MAX_ROWS).contains(&n);
        let not_fat_box = rt.available_ram_gb < FAST_BOX_MIN_RAM_GB;
        let pairs_ok = pairs < SIMPLE_PLAN_MAX_PAIRS;
        let est_pair_gb = (pairs * PAIR_SCORE_BYTES) as f64 / (1024_f64.powi(3));
        let ram_safe = est_pair_gb <= rt.available_ram_gb * BUCKET_RAM_SAFETY_FRACTION;
        row_band && not_fat_box && pairs_ok && ram_safe
    };
    if bucket_suggested_eligible {
        return ExecutionPlan {
            backend: scoring_backend(caps),
            chunk_size: None,
            max_workers: (16_u32).min(rt.cpu_count),
            pair_spill_threshold: None,
            clustering_strategy: ClusteringStrategy::InMemory,
            rule_name: "plan_selected_bucket_suggested".into(),
        };
    }

    // Rule 4: chunked — 50M <= pairs < 5B AND RAM >= 16 GB.
    if (SIMPLE_PLAN_MAX_PAIRS..CHUNKED_MAX_PAIRS).contains(&pairs) && rt.available_ram_gb >= CHUNKED_MIN_RAM_GB {
        return ExecutionPlan {
            backend: BackendName::Chunked,
            chunk_size: Some(auto_chunk_size(n, rt.available_ram_gb)),
            max_workers: (16_u32).min(rt.cpu_count),
            pair_spill_threshold: Some(SpillThreshold::Ram),
            clustering_strategy: ClusteringStrategy::InMemory,
            rule_name: "plan_selected_chunked".into(),
        };
    }

    // Rule 6: ray — n_rows >= 50M AND ray_auto_select AND ray_available.
    // Fails closed: if caps are false, falls through to duckdb.
    if n >= RAY_MIN_ROWS && caps.ray_auto_select && caps.ray_available {
        return ExecutionPlan {
            backend: BackendName::Ray,
            chunk_size: None,
            max_workers: rt.cpu_count,
            pair_spill_threshold: Some(SpillThreshold::DiskPerWorker),
            clustering_strategy: ClusteringStrategy::StreamingCc,
            rule_name: "plan_selected_ray".into(),
        };
    }

    // Rule 5: duckdb — pairs >= 5B OR RAM < 16 GB.  NOT a catch-all; predicate is explicit.
    if pairs >= DUCKDB_MIN_PAIRS || rt.available_ram_gb < DUCKDB_MAX_RAM_GB {
        return ExecutionPlan {
            backend: BackendName::Duckdb,
            chunk_size: None,
            max_workers: DUCKDB_MAX_WORKERS.min(rt.cpu_count),
            pair_spill_threshold: Some(SpillThreshold::Duckdb),
            clustering_strategy: ClusteringStrategy::PartitionedUnionFind,
            rule_name: "plan_selected_duckdb".into(),
        };
    }

    // no_rule_matched — Python parity: `apply_planner_rules` returns
    // `ExecutionPlan(rule_name="no_rule_matched")` when no rule fires,
    // which resolves to the dataclass defaults: polars-direct, max_workers=4
    // (the literal dataclass default, NOT min(4, cpu_count)), everything else None/in_memory.
    ExecutionPlan {
        backend: BackendName::PolarsDirect,
        chunk_size: None,
        max_workers: 4,
        pair_spill_threshold: None,
        clustering_strategy: ClusteringStrategy::InMemory,
        rule_name: "no_rule_matched".into(),
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // Shared fixtures
    fn fat_rt() -> RuntimeProfile {
        RuntimeProfile { available_ram_gb: 64.0, cpu_count: 16, disk_free_gb: 500.0 }
    }
    fn lean_rt() -> RuntimeProfile {
        RuntimeProfile { available_ram_gb: 14.0, cpu_count: 4, disk_free_gb: 200.0 }
    }
    fn mid_rt() -> RuntimeProfile {
        // 16 GB but NOT >= 32 GB fat_box threshold
        RuntimeProfile { available_ram_gb: 16.0, cpu_count: 8, disk_free_gb: 300.0 }
    }
    fn caps_bucket() -> Capabilities {
        Capabilities { bucket_available: true, ray_available: false, ray_auto_select: false, user_backend: None }
    }
    fn caps_plain() -> Capabilities {
        Capabilities { bucket_available: false, ray_available: false, ray_auto_select: false, user_backend: None }
    }

    // ── chunk_size_clamps (A2 test, kept here for continuity) ────────────────
    #[test]
    fn chunk_size_clamps() {
        // Large: clamp upper
        assert_eq!(auto_chunk_size(10_000_000, 64.0), 1_000_000);
        // Small: clamp lower
        assert_eq!(auto_chunk_size(5_000, 64.0), 10_000);
    }

    // ── Rule order + representative outputs (from task spec) ─────────────────
    #[test]
    fn rule_order_and_outputs() {
        let rt = fat_rt();
        let caps = caps_bucket();
        // simple plan: small rows + few pairs → bucket + plan_selected_simple
        let p = decide_plan(&PlannerInput {
            n_rows_full: 50_000,
            estimated_pair_count: 1_000_000,
            runtime: rt,
            caps: caps.clone(),
        });
        assert_eq!(p.backend, BackendName::Bucket);
        assert_eq!(p.rule_name, "plan_selected_simple");
        assert_eq!(p.max_workers, 4);

        // pathological: n_rows=1 → polars-direct + plan_pathological
        let p2 = decide_plan(&PlannerInput {
            n_rows_full: 1,
            estimated_pair_count: 0,
            runtime: rt,
            caps: caps.clone(),
        });
        assert_eq!(p2.rule_name, "plan_pathological");
        assert_eq!(p2.max_workers, 1);
        assert_eq!(p2.backend, BackendName::PolarsDirect);
    }

    // ── Rule 1: pathological ─────────────────────────────────────────────────
    #[test]
    fn rule_pathological_n_rows_zero() {
        let p = decide_plan(&PlannerInput {
            n_rows_full: 0,
            estimated_pair_count: 0,
            runtime: fat_rt(),
            caps: caps_plain(),
        });
        assert_eq!(p.rule_name, "plan_pathological");
        assert_eq!(p.max_workers, 1);
        assert_eq!(p.pair_spill_threshold, None);
        assert_eq!(p.clustering_strategy, ClusteringStrategy::InMemory);
    }

    // ── Rule 2: simple plan ───────────────────────────────────────────────────
    #[test]
    fn rule_simple_bucket_on() {
        // n_rows < 100_000, pairs < 50M → simple; bucket_available=true → Bucket
        let p = decide_plan(&PlannerInput {
            n_rows_full: 99_999,
            estimated_pair_count: 49_999_999,
            runtime: fat_rt(),
            caps: caps_bucket(),
        });
        assert_eq!(p.rule_name, "plan_selected_simple");
        assert_eq!(p.backend, BackendName::Bucket);
        assert_eq!(p.max_workers, 4); // min(4, 16)
        assert_eq!(p.chunk_size, None);
        assert_eq!(p.pair_spill_threshold, None);
        assert_eq!(p.clustering_strategy, ClusteringStrategy::InMemory);
    }

    #[test]
    fn rule_simple_bucket_off() {
        // bucket_available=false → PolarsDirect
        let p = decide_plan(&PlannerInput {
            n_rows_full: 50_000,
            estimated_pair_count: 1_000_000,
            runtime: fat_rt(),
            caps: caps_plain(),
        });
        assert_eq!(p.rule_name, "plan_selected_simple");
        assert_eq!(p.backend, BackendName::PolarsDirect);
    }

    // ── Rule 3: fast_box ──────────────────────────────────────────────────────
    #[test]
    fn rule_fast_box() {
        // n_rows >= 100_000, pairs < 50M, RAM >= 32 GB
        let p = decide_plan(&PlannerInput {
            n_rows_full: 100_000,
            estimated_pair_count: 1_000_000,
            runtime: fat_rt(), // 64 GB
            caps: caps_bucket(),
        });
        assert_eq!(p.rule_name, "plan_selected_fast_box");
        assert_eq!(p.backend, BackendName::Bucket);
        assert_eq!(p.max_workers, 16); // min(16, 16)
        assert_eq!(p.chunk_size, None);
        assert_eq!(p.pair_spill_threshold, None);
        assert_eq!(p.clustering_strategy, ClusteringStrategy::InMemory);
    }

    #[test]
    fn rule_fast_box_cpu_cap() {
        // cpu_count=8 → max_workers = min(16, 8) = 8
        let rt = RuntimeProfile { available_ram_gb: 48.0, cpu_count: 8, disk_free_gb: 300.0 };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 200_000,
            estimated_pair_count: 10_000_000,
            runtime: rt,
            caps: caps_plain(),
        });
        assert_eq!(p.rule_name, "plan_selected_fast_box");
        assert_eq!(p.max_workers, 8);
    }

    // ── Rule 3b: bucket_suggested ─────────────────────────────────────────────
    #[test]
    fn rule_bucket_suggested() {
        // 100k < n_rows <= 750k, RAM < 32 GB, pairs < 50M, RAM-safe
        // pairs=100_000, pair_bytes=64 → 6.4MB → tiny fraction of 16GB
        let p = decide_plan(&PlannerInput {
            n_rows_full: 200_000,
            estimated_pair_count: 100_000,
            runtime: mid_rt(), // 16 GB, NOT >= 32 (so fast_box skips)
            caps: caps_bucket(),
        });
        assert_eq!(p.rule_name, "plan_selected_bucket_suggested");
        assert_eq!(p.backend, BackendName::Bucket);
        assert_eq!(p.max_workers, 8); // min(16, 8)
        assert_eq!(p.chunk_size, None);
        assert_eq!(p.pair_spill_threshold, None);
        assert_eq!(p.clustering_strategy, ClusteringStrategy::InMemory);
    }

    // ── Rule 4: chunked ───────────────────────────────────────────────────────
    #[test]
    fn rule_chunked() {
        // pairs >= 50M, pairs < 5B, RAM >= 16 GB
        let rt = RuntimeProfile { available_ram_gb: 32.0, cpu_count: 16, disk_free_gb: 300.0 };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 5_000_000,
            estimated_pair_count: 100_000_000, // 100M pairs: in chunked band
            runtime: rt,
            caps: caps_plain(),
        });
        assert_eq!(p.rule_name, "plan_selected_chunked");
        assert_eq!(p.backend, BackendName::Chunked);
        assert!(p.chunk_size.is_some());
        assert_eq!(p.pair_spill_threshold, Some(SpillThreshold::Ram));
        assert_eq!(p.clustering_strategy, ClusteringStrategy::InMemory);
        assert_eq!(p.max_workers, 16);
    }

    // ── Rule 6: ray ──────────────────────────────────────────────────────────
    #[test]
    fn rule_ray_caps_on() {
        // n_rows >= 50M, ray_auto_select=true, ray_available=true.
        // For ray to fire, chunked must NOT match first:
        //   chunked predicate: pairs >= 50M AND pairs < 5B AND RAM >= 16 GB.
        // Use pairs >= 5B so chunked is skipped (pairs >= CHUNKED_MAX_PAIRS).
        // Ray fires BEFORE duckdb in the registry order.
        let caps = Capabilities {
            bucket_available: false,
            ray_available: true,
            ray_auto_select: true,
            user_backend: None,
        };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 50_000_000,
            estimated_pair_count: 6_000_000_000, // > 5B: skips chunked, ray fires before duckdb
            runtime: fat_rt(),
            caps,
        });
        assert_eq!(p.rule_name, "plan_selected_ray");
        assert_eq!(p.backend, BackendName::Ray);
        assert_eq!(p.pair_spill_threshold, Some(SpillThreshold::DiskPerWorker));
        assert_eq!(p.clustering_strategy, ClusteringStrategy::StreamingCc);
        assert_eq!(p.max_workers, 16); // cpu_count
    }

    #[test]
    fn rule_ray_caps_off_falls_through_to_duckdb() {
        // ray_auto_select=false → ray predicate fails → duckdb catches it
        // (pairs >= 5B OR RAM < 16 GB; here pairs > 5B)
        let caps = Capabilities {
            bucket_available: false,
            ray_available: true,
            ray_auto_select: false, // gate off
            user_backend: None,
        };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 50_000_000,
            estimated_pair_count: 6_000_000_000, // > 5B → duckdb
            runtime: fat_rt(),
            caps,
        });
        assert_eq!(p.rule_name, "plan_selected_duckdb");
        assert_eq!(p.backend, BackendName::Duckdb);
    }

    // ── Rule 5: duckdb ────────────────────────────────────────────────────────
    #[test]
    fn rule_duckdb_low_ram() {
        // available_ram_gb < 16 → duckdb regardless of pair count
        let p = decide_plan(&PlannerInput {
            n_rows_full: 1_000_000,
            estimated_pair_count: 1_000_000,
            runtime: lean_rt(), // 14 GB
            caps: caps_plain(),
        });
        assert_eq!(p.rule_name, "plan_selected_duckdb");
        assert_eq!(p.backend, BackendName::Duckdb);
        assert_eq!(p.pair_spill_threshold, Some(SpillThreshold::Duckdb));
        assert_eq!(p.clustering_strategy, ClusteringStrategy::PartitionedUnionFind);
        // min(8, 4) = 4
        assert_eq!(p.max_workers, 4);
    }

    #[test]
    fn rule_duckdb_high_pairs() {
        // pairs >= 5B → duckdb even on fat box
        let p = decide_plan(&PlannerInput {
            n_rows_full: 10_000_000,
            estimated_pair_count: 5_000_000_000,
            runtime: fat_rt(),
            caps: caps_plain(),
        });
        assert_eq!(p.rule_name, "plan_selected_duckdb");
        assert_eq!(p.max_workers, 8); // min(8, 16)
    }

    // ── no_rule_matched fallback (Python parity regression) ──────────────────
    /// Concrete divergence from the spec-compliance review:
    ///   n_rows=800_000, pairs=5_000_000, ram=20.0 GB, cpu=8, all caps false/None
    ///   pairs (5M) < 5B AND ram (20) >= 16 → duckdb predicate false.
    ///   Python → backend=polars-direct, rule_name="no_rule_matched", max_workers=4
    ///   Old Rust → duckdb (WRONG: treated duckdb as unconditional catch-all).
    #[test]
    fn rule_no_match_falls_through_to_polars_direct() {
        let rt = RuntimeProfile { available_ram_gb: 20.0, cpu_count: 8, disk_free_gb: 300.0 };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 800_000,
            estimated_pair_count: 5_000_000,
            runtime: rt,
            caps: caps_plain(),
        });
        assert_eq!(p.backend, BackendName::PolarsDirect, "should fall through to no_rule_matched, not duckdb");
        assert_eq!(p.rule_name, "no_rule_matched");
        // Python dataclass default is the literal 4, NOT min(4, cpu_count)
        assert_eq!(p.max_workers, 4);
        assert_eq!(p.pair_spill_threshold, None);
        assert_eq!(p.clustering_strategy, ClusteringStrategy::InMemory);
        assert_eq!(p.chunk_size, None);
    }

    // ── Rule 7 (registry position 0): user_override ───────────────────────────
    #[test]
    fn rule_user_override_polars_direct() {
        let caps = Capabilities {
            bucket_available: true,
            ray_available: false,
            ray_auto_select: false,
            user_backend: Some(BackendName::PolarsDirect),
        };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 50_000,
            estimated_pair_count: 1_000,
            runtime: fat_rt(),
            caps,
        });
        assert_eq!(p.rule_name, "plan_user_override");
        assert_eq!(p.backend, BackendName::PolarsDirect);
        assert_eq!(p.chunk_size, None);
        assert_eq!(p.clustering_strategy, ClusteringStrategy::InMemory);
        assert_eq!(p.max_workers, 16); // min(16, 16)
    }

    #[test]
    fn rule_user_override_chunked_sets_chunk_size() {
        // user sets chunked → chunk_size computed via auto_chunk_size
        let caps = Capabilities {
            bucket_available: false,
            ray_available: false,
            ray_auto_select: false,
            user_backend: Some(BackendName::Chunked),
        };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 500_000,
            estimated_pair_count: 1_000_000,
            runtime: fat_rt(),
            caps,
        });
        assert_eq!(p.rule_name, "plan_user_override");
        assert_eq!(p.backend, BackendName::Chunked);
        assert!(p.chunk_size.is_some());
        assert_eq!(p.chunk_size, Some(auto_chunk_size(500_000, 64.0)));
    }

    #[test]
    fn rule_user_override_beats_pathological() {
        // n_rows=0 would hit pathological, but user_override fires first
        let caps = Capabilities {
            bucket_available: false,
            ray_available: false,
            ray_auto_select: false,
            user_backend: Some(BackendName::Ray),
        };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 0,
            estimated_pair_count: 0,
            runtime: fat_rt(),
            caps,
        });
        assert_eq!(p.rule_name, "plan_user_override");
        assert_eq!(p.backend, BackendName::Ray);
    }

    // ── Threshold boundaries ──────────────────────────────────────────────────
    #[test]
    fn simple_boundary_exactly_at_100k_goes_to_fast_box() {
        // n_rows == 100_000 is NOT < 100_000 → falls through simple → fast_box
        let p = decide_plan(&PlannerInput {
            n_rows_full: 100_000,
            estimated_pair_count: 1_000_000,
            runtime: fat_rt(), // >= 32 GB → fast_box
            caps: caps_plain(),
        });
        assert_eq!(p.rule_name, "plan_selected_fast_box");
    }

    #[test]
    fn simple_boundary_exactly_at_50m_pairs_goes_to_chunked() {
        // pairs == 50_000_000 is NOT < 50M → misses simple AND fast_box → chunked
        let rt = RuntimeProfile { available_ram_gb: 32.0, cpu_count: 8, disk_free_gb: 300.0 };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 50_000, // < 100k, so simple predicate row check passes
            estimated_pair_count: 50_000_000, // NOT < 50M → falls out of simple
            runtime: rt,
            caps: caps_plain(),
        });
        // Doesn't hit simple (pairs not < 50M). Doesn't hit fast_box (n_rows < 100k).
        // Doesn't hit bucket_suggested (n_rows < 100k). Hits chunked (pairs >= 50M, < 5B, RAM >= 16).
        assert_eq!(p.rule_name, "plan_selected_chunked");
    }

    #[test]
    fn duckdb_boundary_at_exactly_5b_pairs() {
        // pairs == 5_000_000_000 → duckdb (>= 5B)
        let rt = RuntimeProfile { available_ram_gb: 64.0, cpu_count: 16, disk_free_gb: 500.0 };
        let p = decide_plan(&PlannerInput {
            n_rows_full: 5_000_000,
            estimated_pair_count: 5_000_000_000,
            runtime: rt,
            caps: caps_plain(),
        });
        assert_eq!(p.rule_name, "plan_selected_duckdb");
    }
}
