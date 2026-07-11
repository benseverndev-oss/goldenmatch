//! Cross-surface parity: the core kernels must reproduce `golden/gc_vectors.json`
//! exactly. The SAME fixture is copied into the TS package by
//! `scripts/build_goldencheck_wasm.mjs`, so the Rust core, the Python
//! `goldencheck-native` wheel, and the TS/WASM surface all validate against one
//! canonical expected set — parity is asserted, not assumed.

use goldencheck_core as gc;
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;

/// null -> 0, first-seen non-null -> 1, 2, 3, … (mirrors the native + wasm shim).
fn intern(col: &[Value]) -> Vec<u64> {
    let mut map: HashMap<String, u64> = HashMap::new();
    let mut next = 1u64;
    col.iter()
        .map(|v| {
            if v.is_null() {
                0
            } else {
                let s = v.as_str().unwrap().to_string();
                *map.entry(s).or_insert_with(|| {
                    let x = next;
                    next += 1;
                    x
                })
            }
        })
        .collect()
}

fn columns(v: &Value) -> Vec<Vec<u64>> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|c| intern(c.as_array().unwrap()))
        .collect()
}

fn as_pairs(v: &Value) -> Vec<(usize, usize)> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|p| {
            let a = p.as_array().unwrap();
            (
                a[0].as_u64().unwrap() as usize,
                a[1].as_u64().unwrap() as usize,
            )
        })
        .collect()
}

#[test]
fn reproduces_golden_vectors() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("golden/gc_vectors.json");
    let fx: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();

    // ── functional dependencies ──────────────────────────────────────────
    let fd = &fx["fd"];
    let cols = columns(&fd["columns"]);
    let refs: Vec<&[u64]> = cols.iter().map(|c| c.as_slice()).collect();
    assert_eq!(
        gc::discover_functional_dependencies_slice(&refs),
        as_pairs(&fd["discover_expected"]),
        "discover_functional_dependencies"
    );
    let approx =
        gc::discover_approximate_fds_slice(&refs, fd["approx_min_confidence"].as_f64().unwrap());
    let approx_want: Vec<(usize, usize, usize)> = fd["approx_expected"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| {
            let a = t.as_array().unwrap();
            (
                a[0].as_u64().unwrap() as usize,
                a[1].as_u64().unwrap() as usize,
                a[2].as_u64().unwrap() as usize,
            )
        })
        .collect();
    assert_eq!(approx, approx_want, "discover_approximate_fds");

    let det = fd["holds_det"].as_u64().unwrap() as usize;
    let dep = fd["holds_dep"].as_u64().unwrap() as usize;
    assert_eq!(
        gc::functional_dependency_holds_slice(&cols[det], &cols[dep]),
        fd["holds_expected"].as_bool().unwrap(),
        "functional_dependency_holds"
    );
    let viol_want: Vec<usize> = fd["violation_rows_expected"]
        .as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_u64().unwrap() as usize)
        .collect();
    assert_eq!(
        gc::fd_violation_rows_slice(&cols[det], &cols[dep]),
        viol_want,
        "fd_violation_rows"
    );

    // ── composite key ────────────────────────────────────────────────────
    let ck = &fx["composite_key"];
    let ck_cols = columns(&ck["columns"]);
    let ck_refs: Vec<&[u64]> = ck_cols.iter().map(|c| c.as_slice()).collect();
    let single_unique: Vec<bool> = ck["single_unique"]
        .as_array()
        .unwrap()
        .iter()
        .map(|b| b.as_bool().unwrap())
        .collect();
    let keys = gc::composite_key_search_slice(
        &ck_refs,
        ck_cols[0].len(),
        ck["max_size"].as_u64().unwrap() as usize,
        &single_unique,
    );
    let keys_want: Vec<Vec<usize>> = ck["expected"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| {
            s.as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_u64().unwrap() as usize)
                .collect()
        })
        .collect();
    assert_eq!(keys, keys_want, "composite_key_search");

    // ── benford ──────────────────────────────────────────────────────────
    let bvals: Vec<f64> = fx["benford"]["values"]
        .as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_f64().unwrap())
        .collect();
    let benford_want: Vec<u64> = fx["benford"]["expected"]
        .as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_u64().unwrap())
        .collect();
    assert_eq!(
        gc::benford_leading_digits_slice(&bvals).to_vec(),
        benford_want,
        "benford_leading_digits"
    );

    // ── near-duplicate clusters ──────────────────────────────────────────
    let nd = &fx["near_dup"];
    let nd_vals: Vec<String> = nd["values"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    let nd_want: Vec<Vec<usize>> = nd["expected"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| {
            c.as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_u64().unwrap() as usize)
                .collect()
        })
        .collect();
    assert_eq!(
        gc::near_duplicate_clusters_slice(&nd_vals, nd["min_similarity"].as_f64().unwrap()),
        nd_want,
        "near_duplicate_clusters"
    );
}
