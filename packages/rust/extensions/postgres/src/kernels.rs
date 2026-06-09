//! Native Core kernel + local-embedding SQL functions for the goldenmatch
//! Postgres extension (#509 — DuckDB<->Postgres lockstep with `core_kernels.py`).
//!
//! The graph functions (`goldenmatch_pair_dedup`, `goldenmatch_connected_components`,
//! and their `_str` siblings) call the pyo3-free `goldenmatch-graph-core` crate
//! **native-direct** — no embedded CPython, same shape as `goldenmatch_record_fingerprint`
//! over `goldenmatch-fingerprint-core`. They take id/score arrays (not JSON) and
//! return relational `TableIterator` rows. `goldenmatch_embed_local` / `gm_embed`
//! likewise call `goldenembed-rs` native-direct (pure Rust, no CPython) and return
//! the embedding vector (`float8[]` / `float4[]` respectively).
//!
//! ```sql
//! SELECT * FROM goldenmatch.goldenmatch_pair_dedup(ARRAY[2,1], ARRAY[1,2], ARRAY[0.5,0.9]);
//! SELECT * FROM goldenmatch.goldenmatch_connected_components(
//!     ARRAY[1,2], ARRAY[2,3], ARRAY[0.9,0.8], ARRAY[1,2,3,4]);
//! SELECT goldenmatch.goldenmatch_embed_local('John Smith', '/path/to/model');
//! -- gm_embed reads GOLDENEMBED_MODEL_DIR from the backend env (float4[]):
//! SELECT goldenmatch.gm_embed('John Smith');
//! ```
use goldenmatch_graph_core as gc;
use pgrx::prelude::*;

/// Native-direct (no CPython): canonical max-score pairs over int64 id arrays.
/// Each pair is canonicalized to `(min, max)` keeping the maximum score.
#[pg_extern]
pub fn goldenmatch_pair_dedup(
    id_a: Vec<i64>,
    id_b: Vec<i64>,
    score: Vec<f64>,
) -> TableIterator<'static, (name!(a, i64), name!(b, i64), name!(s, f64))> {
    let n = id_a.len().min(id_b.len()).min(score.len());
    let pairs: Vec<(i64, i64, f64)> = (0..n).map(|i| (id_a[i], id_b[i], score[i])).collect();
    let out: Vec<(i64, i64, f64)> = gc::dedup_pairs_max_score(&pairs);
    TableIterator::new(out)
}

/// String-id variant of [`goldenmatch_pair_dedup`]: first-seen dict -> int64
/// kernel -> map deduped pairs back to text.
#[pg_extern]
pub fn goldenmatch_pair_dedup_str(
    id_a: Vec<String>,
    id_b: Vec<String>,
    score: Vec<f64>,
) -> TableIterator<'static, (name!(a, String), name!(b, String), name!(s, f64))> {
    let n = id_a.len().min(id_b.len()).min(score.len());
    let mut dict = gc::Dict::new();
    let pairs: Vec<(i64, i64, f64)> = (0..n)
        .map(|i| (dict.intern(&id_a[i]), dict.intern(&id_b[i]), score[i]))
        .collect();
    let out: Vec<(String, String, f64)> = gc::dedup_pairs_max_score(&pairs)
        .into_iter()
        .map(|(a, b, s)| {
            (
                dict.resolve(a).unwrap_or_default().to_string(),
                dict.resolve(b).unwrap_or_default().to_string(),
                s,
            )
        })
        .collect();
    TableIterator::new(out)
}

/// Native-direct connected components over int64 ids. `all_ids` seeds the universe
/// so singletons (ids with no edge) get their own component. Returns one
/// `(component_idx, member)` row per member; components are ordered by their
/// minimum member and members within a component are sorted ascending.
#[pg_extern]
pub fn goldenmatch_connected_components(
    id_a: Vec<i64>,
    id_b: Vec<i64>,
    score: Vec<f64>,
    all_ids: Vec<i64>,
) -> TableIterator<'static, (name!(component, i64), name!(member, i64))> {
    let n = id_a.len().min(id_b.len()).min(score.len());
    let edges: Vec<(i64, i64, f64)> = (0..n).map(|i| (id_a[i], id_b[i], score[i])).collect();
    let mut comps = gc::connected_components(&edges, &all_ids);
    for c in comps.iter_mut() {
        c.sort();
    }
    comps.sort(); // deterministic component order by min member
    let rows: Vec<(i64, i64)> = comps
        .into_iter()
        .enumerate()
        .flat_map(|(ci, members)| members.into_iter().map(move |m| (ci as i64, m)))
        .collect();
    TableIterator::new(rows)
}

/// String-id variant of [`goldenmatch_connected_components`]. `all_ids` is folded
/// into the dict FIRST (stable codes), then edges. Members map back to strings and
/// each component is sorted by the original string ascending before component
/// indices are assigned (so component order is deterministic by min string).
#[pg_extern]
pub fn goldenmatch_connected_components_str(
    id_a: Vec<String>,
    id_b: Vec<String>,
    score: Vec<f64>,
    all_ids: Vec<String>,
) -> TableIterator<'static, (name!(component, i64), name!(member, String))> {
    let n = id_a.len().min(id_b.len()).min(score.len());
    let mut dict = gc::Dict::new();
    // Fold the universe first so its codes are stable, then the edge endpoints.
    let ids: Vec<i64> = all_ids.iter().map(|s| dict.intern(s)).collect();
    let edges: Vec<(i64, i64, f64)> = (0..n)
        .map(|i| (dict.intern(&id_a[i]), dict.intern(&id_b[i]), score[i]))
        .collect();
    let comps = gc::connected_components(&edges, &ids);
    // Map each component to its sorted string members.
    let mut str_comps: Vec<Vec<String>> = comps
        .into_iter()
        .map(|members| {
            let mut s: Vec<String> = members
                .into_iter()
                .map(|m| dict.resolve(m).unwrap_or_default().to_string())
                .collect();
            s.sort();
            s
        })
        .collect();
    str_comps.sort(); // deterministic component order by min string
    let rows: Vec<(i64, String)> = str_comps
        .into_iter()
        .enumerate()
        .flat_map(|(ci, members)| members.into_iter().map(move |m| (ci as i64, m)))
        .collect();
    TableIterator::new(rows)
}

/// Process-lifetime cache of loaded in-house models, keyed by model dir.
///
/// `GoldenEmbed::load` reads an ONNX model from disk -- doing it per call (the
/// pre-#737 behavior) reloaded the model on every row. Each Postgres backend
/// process gets its own `OnceLock` (forked after postmaster start), so the model
/// loads once per backend on first use, mirroring the DataFusion UDF's
/// `Arc<Mutex<GoldenEmbed>>` posture (`embed_udf.rs`). `ort`'s `Session::run` is
/// `&mut`, so the same single-`Mutex` serialization applies.
fn embed_models(
) -> &'static std::sync::Mutex<std::collections::HashMap<String, goldenembed::GoldenEmbed>> {
    static MODELS: std::sync::OnceLock<
        std::sync::Mutex<std::collections::HashMap<String, goldenembed::GoldenEmbed>>,
    > = std::sync::OnceLock::new();
    MODELS.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}

/// Embed one text against the cached model at `model_dir`. Loads + caches on
/// first use per backend process. Returns the raw `f32` components.
fn embed_one(model_dir: &str, text: &str) -> Vec<f32> {
    let mut models = embed_models()
        .lock()
        .unwrap_or_else(|_| pgrx::error!("gm_embed: model cache lock poisoned"));
    let model = models.entry(model_dir.to_string()).or_insert_with(|| {
        goldenembed::GoldenEmbed::load(model_dir)
            .unwrap_or_else(|e| pgrx::error!("gm_embed load '{}': {}", model_dir, e))
    });
    match model.embed(&[text]) {
        Ok(rows) => rows.into_iter().next().unwrap_or_default(),
        Err(e) => pgrx::error!("gm_embed embed: {}", e),
    }
}

/// Embed one text with a local in-house model via goldenembed-rs (pure Rust,
/// NO embedded CPython). `model_path` is a saved GoldenEmbedModel dir. Returns
/// the embedding vector as float8[]. The model is loaded once per backend
/// process and cached by path (#737).
#[pg_extern]
pub fn goldenmatch_embed_local(text: String, model_path: String) -> Vec<f64> {
    embed_one(&model_path, &text)
        .into_iter()
        .map(|x| x as f64)
        .collect()
}

/// Embed one text with the in-house model, model dir resolved from the
/// `GOLDENEMBED_MODEL_DIR` env var (loaded once per backend process). Returns
/// `float4[]` -- parity with the DataFusion `goldenmatch_embed` UDF, including
/// the NULL -> "" convention (so the arg is nullable, NOT `STRICT`). #737.
#[pg_extern]
pub fn gm_embed(text: Option<&str>) -> Vec<f32> {
    let dir = std::env::var("GOLDENEMBED_MODEL_DIR").unwrap_or_else(|_| {
        pgrx::error!(
            "gm_embed: GOLDENEMBED_MODEL_DIR not set (a saved GoldenEmbedModel \
             directory). Use goldenmatch_embed_local(text, model_path) to pass \
             the dir explicitly."
        )
    });
    embed_one(&dir, text.unwrap_or(""))
}

/// Canonical record fingerprint (64 lowercase hex) of a JSON record object.
/// The cross-surface stable record-id hash — same value the DuckDB
/// `goldenmatch_record_fingerprint` UDF, the native C ABI, and the Python
/// identity path produce. `__`-prefixed keys are dropped.
///
/// Computed **in pure Rust** via `goldenmatch-fingerprint-core` — NOT through
/// the embedded-CPython bridge. This is the first SQL function that needs no
/// interpreter for its work (the decoupling lever).
///
/// ```sql
/// SELECT goldenmatch.goldenmatch_record_fingerprint('{"first":"Alex","last":"Smith"}');
/// ```
#[pg_extern]
pub fn goldenmatch_record_fingerprint(record_json: String) -> String {
    match goldenmatch_fingerprint_core::fingerprint_json(&record_json) {
        Ok(hex) => hex,
        Err(e) => pgrx::error!("goldenmatch_record_fingerprint: {}", e),
    }
}

#[cfg(any(test, feature = "pg_test"))]
#[pgrx::pg_schema]
mod tests {
    use pgrx::prelude::*;

    /// pgrx computes the canonical fingerprint in pure Rust; assert it matches
    /// the pinned vector shared with the Python + native + DuckDB surfaces.
    #[pg_test]
    fn record_fingerprint_matches_pinned() {
        let got = crate::kernels::goldenmatch_record_fingerprint(r#"{"a":"x"}"#.to_string());
        assert_eq!(
            got,
            "7381d5ba2dac5be0af49232a3209ab8d0dc2e4ed804a60ce533fdfe5254307e3"
        );
    }

    /// Native-direct int64 pair dedup: canonicalizes `(2,1)`/`(1,2)` to `(1,2)`
    /// and keeps the max score.
    #[pg_test]
    fn pair_dedup_int_native() {
        let rows: Vec<(i64, i64, f64)> =
            crate::kernels::goldenmatch_pair_dedup(vec![2, 1], vec![1, 2], vec![0.5, 0.9])
                .collect();
        assert_eq!(rows, vec![(1, 2, 0.9)]);
    }

    /// String-id pair dedup round-trips through the first-seen dict.
    #[pg_test]
    fn pair_dedup_str_native() {
        let rows: Vec<(String, String, f64)> = crate::kernels::goldenmatch_pair_dedup_str(
            vec!["b".to_string(), "a".to_string()],
            vec!["a".to_string(), "b".to_string()],
            vec![0.5, 0.9],
        )
        .collect();
        // first-seen intern: "b"=0, "a"=1; canonical (min,max)=(0,1)=("b","a").
        assert_eq!(rows, vec![("b".to_string(), "a".to_string(), 0.9)]);
    }

    /// Connected components seeds singletons from `all_ids`: {1,2,3} and {4}.
    #[pg_test]
    fn connected_components_int_includes_singleton() {
        let rows: Vec<(i64, i64)> = crate::kernels::goldenmatch_connected_components(
            vec![1, 2],
            vec![2, 3],
            vec![0.9, 0.8],
            vec![1, 2, 3, 4],
        )
        .collect();
        let comp_of = |m: i64| rows.iter().find(|(_, x)| *x == m).unwrap().0;
        assert_eq!(comp_of(1), comp_of(2));
        assert_eq!(comp_of(2), comp_of(3));
        assert_ne!(comp_of(1), comp_of(4));
    }

    /// String connected components: deterministic component order by min string,
    /// members sorted ascending within a component.
    #[pg_test]
    fn connected_components_str_includes_singleton() {
        let rows: Vec<(i64, String)> = crate::kernels::goldenmatch_connected_components_str(
            vec!["x".to_string(), "y".to_string()],
            vec!["y".to_string(), "z".to_string()],
            vec![0.9, 0.8],
            vec![
                "x".to_string(),
                "y".to_string(),
                "z".to_string(),
                "w".to_string(),
            ],
        )
        .collect();
        // component 0 = {w} (min string "w"), component 1 = {x,y,z}.
        assert_eq!(rows[0], (0, "w".to_string()));
        let comp_of = |m: &str| rows.iter().find(|(_, x)| x == m).unwrap().0;
        assert_eq!(comp_of("x"), comp_of("y"));
        assert_eq!(comp_of("y"), comp_of("z"));
        assert_ne!(comp_of("x"), comp_of("w"));
    }

    /// Empty edge list: every id is its own singleton component.
    #[pg_test]
    fn connected_components_all_singletons_when_no_edges() {
        let rows: Vec<(i64, i64)> = crate::kernels::goldenmatch_connected_components(
            vec![],
            vec![],
            vec![],
            vec![10, 20, 30],
        )
        .collect();
        let labels: std::collections::HashSet<i64> = rows.iter().map(|(c, _)| *c).collect();
        assert_eq!(rows.len(), 3);
        assert_eq!(labels.len(), 3);
    }

    /// Fingerprint is stable across key order (canonicalization sorts fields).
    #[pg_test]
    fn record_fingerprint_is_key_order_independent() {
        let a = crate::kernels::goldenmatch_record_fingerprint(r#"{"x":"1","y":"2"}"#.to_string());
        let b = crate::kernels::goldenmatch_record_fingerprint(r#"{"y":"2","x":"1"}"#.to_string());
        assert_eq!(a, b);
    }

    /// dedup keeps the max score across duplicate canonical pairs.
    #[pg_test]
    fn pair_dedup_keeps_max_across_duplicates() {
        let rows: Vec<(i64, i64, f64)> = crate::kernels::goldenmatch_pair_dedup(
            vec![1, 1, 2],
            vec![2, 2, 1],
            vec![0.3, 0.7, 0.5],
        )
        .collect();
        assert_eq!(rows, vec![(1, 2, 0.7)]);
    }
}
