//! Author the cross-surface parity fixtures from the HOST boundary
//! (`resolve_json_impl` == `goldenprofile_core::resolve_json`), which is the
//! same kernel the Python `goldenprofile_native` wheel and the `wasm32` build
//! wrap. So this emits the single source-of-truth `{request, expected}` set the
//! TS wasm parity test and the Python cross-parity test both assert against.
//!
//! Run (writes the committed fixture file):
//!   cargo run -p goldenprofile-wasm --example gen_parity_fixtures > \
//!     ../../typescript/goldenprofile/tests/parity/fixtures/goldenprofile/resolutions.json
//!
//! Re-run whenever the kernel changes; CI guards the committed file vs a rebuild.

use serde_json::{json, Value};

/// One profile element as the request JSON expects it.
fn p(kind: &str, name: &str, category: &str, anchor: &str, attribute: &str) -> Value {
    json!({"kind": kind, "name": name, "category": category, "anchor": anchor, "attribute": attribute})
}

/// Canonicalize a `Resolution` so the committed fixture is stable.
///
/// The kernel's cluster/edge ORDERING falls out of hash-map iteration order and
/// is nondeterministic run-to-run (Rust `HashMap` random seed), so a raw dump
/// would differ on every regen. The cross-surface invariant is the PARTITION +
/// the edge set, not the ordering — so we sort members within each cluster,
/// sort the clusters, put `a<=b` in each edge, and sort the edge list. The TS
/// and Python parity tests apply the SAME canonicalization to both sides.
fn canonicalize(mut resolution: Value) -> Value {
    if let Some(clusters) = resolution["clusters"].as_array() {
        let mut cs: Vec<Vec<i64>> = clusters
            .iter()
            .map(|c| {
                let mut m: Vec<i64> = c
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|x| x.as_i64().unwrap())
                    .collect();
                m.sort_unstable();
                m
            })
            .collect();
        cs.sort_unstable_by(|x, y| x[0].cmp(&y[0]).then(x.len().cmp(&y.len())));
        resolution["clusters"] = json!(cs);
    }
    if let Some(edges) = resolution["edges"].as_array() {
        let mut es: Vec<Value> = edges
            .iter()
            .map(|e| {
                let (mut a, mut b) = (e["a"].as_i64().unwrap(), e["b"].as_i64().unwrap());
                if a > b {
                    std::mem::swap(&mut a, &mut b);
                }
                let mut e2 = e.clone();
                e2["a"] = json!(a);
                e2["b"] = json!(b);
                e2
            })
            .collect();
        es.sort_by(|x, y| {
            (x["a"].as_i64().unwrap(), x["b"].as_i64().unwrap())
                .cmp(&(y["a"].as_i64().unwrap(), y["b"].as_i64().unwrap()))
        });
        resolution["edges"] = json!(es);
    }
    resolution
}

fn case(name: &str, profiles: Vec<Value>) -> Value {
    let request = json!({ "profiles": profiles });
    let request_str = serde_json::to_string(&request).unwrap();
    let expected_str = goldenprofile_wasm::resolve_json_impl(&request_str)
        .unwrap_or_else(|e| panic!("resolve_json failed for case {name}: {e}"));
    let expected: Value = serde_json::from_str(&expected_str).unwrap();
    json!({ "name": name, "request": request, "expected": canonicalize(expected) })
}

fn main() {
    let mut cases: Vec<Value> = Vec::new();

    // 1. clean two-document merge -> one entity.
    cases.push(case(
        "clean_merge",
        vec![
            p("node", "Acme Inc", "Company", "UNKNOWN", "Anvils"),
            p("node", "Acme", "Company", "UNKNOWN", "Founded 1900"),
        ],
    ));

    // 2. node vs edge of the same surface text must NEVER cross-merge.
    cases.push(case(
        "node_edge_no_cross",
        vec![
            p("node", "Acme", "Company", "UNKNOWN", "x"),
            p("edge", "Acme", "Company", "UNKNOWN", "x"),
        ],
    ));

    // 3. lone profile -> a singleton cluster.
    cases.push(case(
        "singleton",
        vec![p("node", "Solo Corp", "Company", "UNKNOWN", "only one")],
    ));

    // 4. clearly distinct entities stay split (anti-over-merge).
    cases.push(case(
        "distinct_stay_split",
        vec![
            p("node", "Acme", "Company", "UNKNOWN", "anvils"),
            p("node", "Globex", "Company", "UNKNOWN", "rockets"),
        ],
    ));

    // 5. transitive evidence A~B, B~C collapses to one entity.
    cases.push(case(
        "transitive_chain",
        vec![
            p(
                "node",
                "International Business Machines",
                "Company",
                "UNKNOWN",
                "mainframes",
            ),
            p("node", "IBM Corp", "Company", "UNKNOWN", "mainframes"),
            p("node", "IBM", "Company", "UNKNOWN", "mainframes"),
        ],
    ));

    // 6. non-BMP unicode must survive the JSON boundary (codepoint iteration).
    cases.push(case(
        "unicode_nonbmp",
        vec![
            p("node", "Acmé 😀 Inc", "Company", "UNKNOWN", "emoji"),
            p("node", "Acmé 😀", "Company", "UNKNOWN", "emoji co"),
        ],
    ));

    // 7. people kind with shared anchors (a different category path).
    cases.push(case(
        "people_shared_anchor",
        vec![
            p("node", "Jane Q Smith", "Person", "Acme Inc", "engineer"),
            p("node", "Jane Smith", "Person", "Acme Inc", "engineer"),
            p("node", "John Doe", "Person", "Globex", "manager"),
        ],
    ));

    let out = json!({ "cases": cases });
    println!("{}", serde_json::to_string_pretty(&out).unwrap());
}
