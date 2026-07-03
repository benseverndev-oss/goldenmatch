//! Pyo3-free graph kernels. Behavior-exact extraction of the loops that lived in
//! `native/src/{cluster,pairs}.rs`; the `native` crate keeps thin `#[pyfunction]`
//! shims delegating here (one source of truth, like `score-core`).
mod dict;
pub use dict::*;

use std::collections::BTreeMap;
use std::collections::HashMap;

#[cfg(feature = "arrow")]
use arrow::array::builder::{Int64Builder, ListBuilder, StringBuilder};
#[cfg(feature = "arrow")]
use arrow::array::{Array, ArrayData, Float64Array, Int64Array, ListArray, StringArray};
#[cfg(feature = "arrow")]
use arrow::datatypes::DataType;

/// Canonicalize each pair as `(min,max)` and keep the max score per pair.
/// Behavior-exact port of `native::pairs::dedup_pairs_max_score`.
pub fn dedup_pairs_max_score(pairs: &[(i64, i64, f64)]) -> Vec<(i64, i64, f64)> {
    let mut best: BTreeMap<(i64, i64), f64> = BTreeMap::new();
    for &(a, b, s) in pairs {
        let key = if a <= b { (a, b) } else { (b, a) };
        match best.get(&key) {
            Some(&cur) if s <= cur => {}
            _ => {
                best.insert(key, s);
            }
        }
    }
    best.into_iter().map(|((a, b), s)| (a, b, s)).collect()
}

/// Iterative find with path compression over a `HashMap` parent table.
fn find(parent: &mut HashMap<i64, i64>, x: i64) -> i64 {
    let mut root = x;
    while let Some(&p) = parent.get(&root) {
        if p == root {
            break;
        }
        root = p;
    }
    // Path-compress x..root.
    let mut cur = x;
    while let Some(&p) = parent.get(&cur) {
        if p == root {
            break;
        }
        parent.insert(cur, root);
        cur = p;
    }
    root
}

/// Connected components over `all_ids` ∪ edge endpoints. Behavior-exact port of
/// `native::cluster::connected_components`. Component membership is independent
/// of union strategy, so naive union yields the identical grouping the Python
/// union-by-rank produces. Component and member order is irrelevant.
pub fn connected_components(edges: &[(i64, i64, f64)], all_ids: &[i64]) -> Vec<Vec<i64>> {
    let mut parent: HashMap<i64, i64> = HashMap::with_capacity(all_ids.len());
    for &id in all_ids {
        parent.entry(id).or_insert(id);
    }
    for (a, b, _s) in edges {
        parent.entry(*a).or_insert(*a);
        parent.entry(*b).or_insert(*b);
    }
    for (a, b, _s) in edges {
        let ra = find(&mut parent, *a);
        let rb = find(&mut parent, *b);
        if ra != rb {
            parent.insert(ra, rb);
        }
    }
    let keys: Vec<i64> = parent.keys().copied().collect();
    let mut groups: HashMap<i64, Vec<i64>> = HashMap::new();
    for k in keys {
        let r = find(&mut parent, k);
        groups.entry(r).or_default().push(k);
    }
    groups.into_values().collect()
}

/// Deterministic label-propagation communities over `all_ids` ∪ edge endpoints.
///
/// Each id starts in its own community. Repeatedly, in ASCENDING id order, each
/// id adopts the most frequent label among its (set) neighbors — ties broken by
/// the smallest label; a node with no neighbors keeps its own. Updates are
/// applied in place (asynchronous), so the processed/unprocessed split is fixed
/// by ascending id, making the result independent of input edge/id order. The
/// sweep repeats until a full pass makes no change or `max_iters` is hit (the
/// hard termination backstop). Adjacency is a SET — duplicate edges collapse and
/// self-loops `(a,a)` are ignored (a node never votes for its own label). Edge
/// weight is ignored (unweighted LP). Returns communities sorted by minimum id,
/// each member list sorted ascending; singletons included (parity with
/// `connected_components`).
///
/// Label propagation is intentionally a modest first kernel: on small or densely
/// connected graphs it tends to merge across bridges (community ≈ connected
/// component). A modularity-optimal method (Leiden) behind the same return shape
/// is the future quality upgrade.
pub fn label_propagation_communities(
    edges: &[(i64, i64, f64)],
    all_ids: &[i64],
    max_iters: u32,
) -> Vec<Vec<i64>> {
    use std::collections::BTreeSet;

    // Set adjacency over the id universe ∪ edge endpoints; drop self-loops.
    let mut adj: BTreeMap<i64, BTreeSet<i64>> = BTreeMap::new();
    for &id in all_ids {
        adj.entry(id).or_default();
    }
    for &(a, b, _s) in edges {
        adj.entry(a).or_default();
        adj.entry(b).or_default();
        if a != b {
            adj.get_mut(&a).unwrap().insert(b);
            adj.get_mut(&b).unwrap().insert(a);
        }
    }

    // Each node starts as its own label. BTreeMap keys are ascending.
    let nodes: Vec<i64> = adj.keys().copied().collect();
    let mut label: BTreeMap<i64, i64> = nodes.iter().map(|&id| (id, id)).collect();

    for _ in 0..max_iters {
        let mut changed = false;
        for &v in &nodes {
            let neighbors = &adj[&v];
            if neighbors.is_empty() {
                continue; // no neighbors → keep own label
            }
            // Tally neighbor labels (current/in-place values).
            let mut freq: BTreeMap<i64, usize> = BTreeMap::new();
            for &u in neighbors {
                *freq.entry(label[&u]).or_insert(0) += 1;
            }
            // Most frequent; ascending-label iteration + strict `>` keeps the
            // SMALLEST label among those tied for the max count.
            let mut best_label = v;
            let mut best_count = 0usize;
            for (&lab, &cnt) in &freq {
                if cnt > best_count {
                    best_count = cnt;
                    best_label = lab;
                }
            }
            if best_label != label[&v] {
                label.insert(v, best_label);
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }

    // Group by final label; members ascending (nodes iterated ascending).
    let mut groups: BTreeMap<i64, Vec<i64>> = BTreeMap::new();
    for &v in &nodes {
        groups.entry(label[&v]).or_default().push(v);
    }
    let mut out: Vec<Vec<i64>> = groups.into_values().collect();
    out.sort_by_key(|c| c[0]); // c[0] is the min member (ascending push order)
    out
}

/// Arrow columnar dedup over int64 id columns + float64 score. Validates types,
/// reads the columns, runs `dedup_pairs_max_score`, returns three Arrow arrays.
#[cfg(feature = "arrow")]
pub fn dedup_pairs_arrow_data(
    id_a: ArrayData,
    id_b: ArrayData,
    score: ArrayData,
) -> Result<(ArrayData, ArrayData, ArrayData), String> {
    if id_a.data_type() != &DataType::Int64 {
        return Err(format!(
            "dedup_pairs_arrow_data: id_a column must be Int64, got {:?}",
            id_a.data_type()
        ));
    }
    if id_b.data_type() != &DataType::Int64 {
        return Err(format!(
            "dedup_pairs_arrow_data: id_b column must be Int64, got {:?}",
            id_b.data_type()
        ));
    }
    if score.data_type() != &DataType::Float64 {
        return Err(format!(
            "dedup_pairs_arrow_data: score column must be Float64, got {:?}",
            score.data_type()
        ));
    }
    if id_a.len() != id_b.len() || id_a.len() != score.len() {
        return Err(format!(
            "dedup_pairs_arrow_data: column length mismatch (id_a={}, id_b={}, score={})",
            id_a.len(),
            id_b.len(),
            score.len()
        ));
    }
    let id_a = Int64Array::from(id_a);
    let id_b = Int64Array::from(id_b);
    let score = Float64Array::from(score);
    let mut pairs: Vec<(i64, i64, f64)> = Vec::with_capacity(id_a.len());
    for i in 0..id_a.len() {
        if id_a.is_null(i) || id_b.is_null(i) || score.is_null(i) {
            continue;
        }
        pairs.push((id_a.value(i), id_b.value(i), score.value(i)));
    }
    let deduped = dedup_pairs_max_score(&pairs);
    let mut out_a = Vec::with_capacity(deduped.len());
    let mut out_b = Vec::with_capacity(deduped.len());
    let mut out_s = Vec::with_capacity(deduped.len());
    for (a, b, s) in deduped {
        out_a.push(a);
        out_b.push(b);
        out_s.push(s);
    }
    Ok((
        Int64Array::from(out_a).to_data(),
        Int64Array::from(out_b).to_data(),
        Float64Array::from(out_s).to_data(),
    ))
}

/// String-id dedup: id columns are Utf8 (StringArray). Builds a first-seen Dict,
/// runs the i64 kernel, maps the deduped pairs back to the original strings.
#[cfg(feature = "arrow")]
pub fn dedup_pairs_arrow_data_utf8(
    id_a: ArrayData,
    id_b: ArrayData,
    score: ArrayData,
) -> Result<(ArrayData, ArrayData, ArrayData), String> {
    if id_a.data_type() != &DataType::Utf8 {
        return Err(format!(
            "dedup_pairs_arrow_data_utf8: id_a column must be Utf8, got {:?}",
            id_a.data_type()
        ));
    }
    if id_b.data_type() != &DataType::Utf8 {
        return Err(format!(
            "dedup_pairs_arrow_data_utf8: id_b column must be Utf8, got {:?}",
            id_b.data_type()
        ));
    }
    if score.data_type() != &DataType::Float64 {
        return Err(format!(
            "dedup_pairs_arrow_data_utf8: score column must be Float64, got {:?}",
            score.data_type()
        ));
    }
    if id_a.len() != id_b.len() || id_a.len() != score.len() {
        return Err(format!(
            "dedup_pairs_arrow_data_utf8: column length mismatch (id_a={}, id_b={}, score={})",
            id_a.len(),
            id_b.len(),
            score.len()
        ));
    }
    let id_a = StringArray::from(id_a);
    let id_b = StringArray::from(id_b);
    let score = Float64Array::from(score);
    let mut dict = Dict::new();
    let mut pairs: Vec<(i64, i64, f64)> = Vec::with_capacity(id_a.len());
    for i in 0..id_a.len() {
        if id_a.is_null(i) || id_b.is_null(i) || score.is_null(i) {
            continue;
        }
        let a = dict.intern(id_a.value(i));
        let b = dict.intern(id_b.value(i));
        pairs.push((a, b, score.value(i)));
    }
    let deduped = dedup_pairs_max_score(&pairs);
    let mut out_a: Vec<&str> = Vec::with_capacity(deduped.len());
    let mut out_b: Vec<&str> = Vec::with_capacity(deduped.len());
    let mut out_s: Vec<f64> = Vec::with_capacity(deduped.len());
    for (a, b, s) in &deduped {
        let sa = dict.resolve(*a).ok_or_else(|| {
            format!("dedup_pairs_arrow_data_utf8: unresolved interned id {a}")
        })?;
        let sb = dict.resolve(*b).ok_or_else(|| {
            format!("dedup_pairs_arrow_data_utf8: unresolved interned id {b}")
        })?;
        out_a.push(sa);
        out_b.push(sb);
        out_s.push(*s);
    }
    Ok((
        StringArray::from(out_a).to_data(),
        StringArray::from(out_b).to_data(),
        Float64Array::from(out_s).to_data(),
    ))
}

/// Arrow columnar connected components. Edge columns int64/int64/float64 + an
/// int64 `all_ids` universe column. Returns ONE Arrow `List<Int64>` array: one
/// list element per component, each a sorted list of member ids.
#[cfg(feature = "arrow")]
pub fn connected_components_arrow_data(
    id_a: ArrayData,
    id_b: ArrayData,
    score: ArrayData,
    all_ids: ArrayData,
) -> Result<ArrayData, String> {
    if id_a.data_type() != &DataType::Int64 {
        return Err(format!(
            "connected_components_arrow_data: id_a column must be Int64, got {:?}",
            id_a.data_type()
        ));
    }
    if id_b.data_type() != &DataType::Int64 {
        return Err(format!(
            "connected_components_arrow_data: id_b column must be Int64, got {:?}",
            id_b.data_type()
        ));
    }
    if score.data_type() != &DataType::Float64 {
        return Err(format!(
            "connected_components_arrow_data: score column must be Float64, got {:?}",
            score.data_type()
        ));
    }
    if all_ids.data_type() != &DataType::Int64 {
        return Err(format!(
            "connected_components_arrow_data: all_ids column must be Int64, got {:?}",
            all_ids.data_type()
        ));
    }
    if id_a.len() != id_b.len() || id_a.len() != score.len() {
        return Err(format!(
            "connected_components_arrow_data: edge column length mismatch (id_a={}, id_b={}, score={})",
            id_a.len(),
            id_b.len(),
            score.len()
        ));
    }
    let id_a = Int64Array::from(id_a);
    let id_b = Int64Array::from(id_b);
    let score = Float64Array::from(score);
    let all_ids = Int64Array::from(all_ids);
    let mut edges: Vec<(i64, i64, f64)> = Vec::with_capacity(id_a.len());
    for i in 0..id_a.len() {
        if id_a.is_null(i) || id_b.is_null(i) || score.is_null(i) {
            continue;
        }
        edges.push((id_a.value(i), id_b.value(i), score.value(i)));
    }
    let mut ids: Vec<i64> = Vec::with_capacity(all_ids.len());
    for i in 0..all_ids.len() {
        if all_ids.is_null(i) {
            continue;
        }
        ids.push(all_ids.value(i));
    }
    let mut comps = connected_components(&edges, &ids);
    for c in comps.iter_mut() {
        c.sort_unstable();
    }
    let mut builder = ListBuilder::new(Int64Builder::new());
    for comp in &comps {
        for &member in comp {
            builder.values().append_value(member);
        }
        builder.append(true);
    }
    let list: ListArray = builder.finish();
    Ok(list.to_data())
}

/// String-id connected components. Edge + universe columns are Utf8. Interns via
/// a first-seen Dict, runs the i64 kernel, maps members back to strings, sorts
/// each component by the original string ascending, returns a `List<Utf8>`.
#[cfg(feature = "arrow")]
pub fn connected_components_arrow_data_utf8(
    id_a: ArrayData,
    id_b: ArrayData,
    score: ArrayData,
    all_ids: ArrayData,
) -> Result<ArrayData, String> {
    if id_a.data_type() != &DataType::Utf8 {
        return Err(format!(
            "connected_components_arrow_data_utf8: id_a column must be Utf8, got {:?}",
            id_a.data_type()
        ));
    }
    if id_b.data_type() != &DataType::Utf8 {
        return Err(format!(
            "connected_components_arrow_data_utf8: id_b column must be Utf8, got {:?}",
            id_b.data_type()
        ));
    }
    if score.data_type() != &DataType::Float64 {
        return Err(format!(
            "connected_components_arrow_data_utf8: score column must be Float64, got {:?}",
            score.data_type()
        ));
    }
    if all_ids.data_type() != &DataType::Utf8 {
        return Err(format!(
            "connected_components_arrow_data_utf8: all_ids column must be Utf8, got {:?}",
            all_ids.data_type()
        ));
    }
    if id_a.len() != id_b.len() || id_a.len() != score.len() {
        return Err(format!(
            "connected_components_arrow_data_utf8: edge column length mismatch (id_a={}, id_b={}, score={})",
            id_a.len(),
            id_b.len(),
            score.len()
        ));
    }
    let id_a = StringArray::from(id_a);
    let id_b = StringArray::from(id_b);
    let score = Float64Array::from(score);
    let all_ids = StringArray::from(all_ids);
    let mut dict = Dict::new();
    let mut edges: Vec<(i64, i64, f64)> = Vec::with_capacity(id_a.len());
    for i in 0..id_a.len() {
        if id_a.is_null(i) || id_b.is_null(i) || score.is_null(i) {
            continue;
        }
        let a = dict.intern(id_a.value(i));
        let b = dict.intern(id_b.value(i));
        edges.push((a, b, score.value(i)));
    }
    let mut ids: Vec<i64> = Vec::with_capacity(all_ids.len());
    for i in 0..all_ids.len() {
        if all_ids.is_null(i) {
            continue;
        }
        ids.push(dict.intern(all_ids.value(i)));
    }
    let comps = connected_components(&edges, &ids);
    let mut builder = ListBuilder::new(StringBuilder::new());
    for comp in &comps {
        let mut members: Vec<&str> = Vec::with_capacity(comp.len());
        for &id in comp {
            let s = dict.resolve(id).ok_or_else(|| {
                format!("connected_components_arrow_data_utf8: unresolved interned id {id}")
            })?;
            members.push(s);
        }
        members.sort_unstable();
        for m in members {
            builder.values().append_value(m);
        }
        builder.append(true);
    }
    let list: ListArray = builder.finish();
    Ok(list.to_data())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dedup_keeps_max_and_canonicalizes() {
        let got = dedup_pairs_max_score(&[(2, 1, 0.5), (1, 2, 0.9), (3, 3, 0.1)]);
        assert_eq!(got, vec![(1, 2, 0.9), (3, 3, 0.1)]);
    }

    #[test]
    fn cc_groups_transitive_and_includes_singletons() {
        let comps = connected_components(&[(1, 2, 0.9), (2, 3, 0.8)], &[1, 2, 3, 4]);
        let mut sorted: Vec<Vec<i64>> = comps.iter().map(|c| { let mut v = c.clone(); v.sort(); v }).collect();
        sorted.sort();
        assert_eq!(sorted, vec![vec![1, 2, 3], vec![4]]);
    }

    #[test]
    fn lp_disconnected_components_and_singletons() {
        // two separate triangles + an isolated node 6
        let edges = [
            (0, 1, 1.0), (1, 2, 1.0), (0, 2, 1.0),
            (3, 4, 1.0), (4, 5, 1.0), (3, 5, 1.0),
        ];
        let got = label_propagation_communities(&edges, &[0, 1, 2, 3, 4, 5, 6], 100);
        assert_eq!(got, vec![vec![0, 1, 2], vec![3, 4, 5], vec![6]]);
    }

    #[test]
    fn lp_connected_graph_one_community() {
        let got = label_propagation_communities(&[(0, 1, 1.0), (1, 2, 1.0)], &[0, 1, 2], 100);
        assert_eq!(got, vec![vec![0, 1, 2]]);
    }

    #[test]
    fn lp_deterministic_under_input_permutation() {
        let edges_a = [(0, 1, 1.0), (1, 2, 1.0), (3, 4, 1.0)];
        let edges_b = [(4, 3, 1.0), (2, 1, 1.0), (1, 0, 1.0)]; // shuffled + reversed
        let a = label_propagation_communities(&edges_a, &[0, 1, 2, 3, 4], 100);
        let b = label_propagation_communities(&edges_b, &[4, 3, 2, 1, 0], 100);
        assert_eq!(a, b);
    }

    #[test]
    fn lp_dup_edges_and_self_loops_ignored() {
        let plain = label_propagation_communities(&[(0, 1, 1.0), (1, 2, 1.0)], &[0, 1, 2], 100);
        let noisy = label_propagation_communities(
            &[(0, 1, 1.0), (1, 0, 1.0), (1, 2, 1.0), (2, 2, 1.0), (0, 0, 1.0)],
            &[0, 1, 2],
            100,
        );
        assert_eq!(plain, noisy);
    }

    #[test]
    fn lp_max_iters_cutoff_returns_well_formed_partition() {
        // chain that may need several sweeps; cap at 1 -> still a valid partition
        let got = label_propagation_communities(&[(0, 1, 1.0), (1, 2, 1.0), (2, 3, 1.0)], &[0, 1, 2, 3], 1);
        // every id assigned exactly once
        let mut all: Vec<i64> = got.iter().flatten().copied().collect();
        all.sort();
        assert_eq!(all, vec![0, 1, 2, 3]);
    }

    #[cfg(feature = "arrow")]
    use arrow::array::{Array, Float64Array, Int64Array, ListArray, StringArray};

    #[cfg(feature = "arrow")]
    #[test]
    fn dedup_arrow_int64_canonicalizes_and_keeps_max() {
        let id_a = Int64Array::from(vec![2_i64, 1]).to_data();
        let id_b = Int64Array::from(vec![1_i64, 2]).to_data();
        let score = Float64Array::from(vec![0.5_f64, 0.9]).to_data();
        let (out_a, out_b, out_s) = dedup_pairs_arrow_data(id_a, id_b, score).unwrap();
        let a = Int64Array::from(out_a);
        let b = Int64Array::from(out_b);
        let s = Float64Array::from(out_s);
        assert_eq!(a.len(), 1);
        assert_eq!(a.value(0), 1);
        assert_eq!(b.value(0), 2);
        assert!((s.value(0) - 0.9).abs() < 1e-12);
    }

    #[cfg(feature = "arrow")]
    #[test]
    fn dedup_arrow_utf8_maps_back_to_strings() {
        let id_a = StringArray::from(vec!["b", "a"]).to_data();
        let id_b = StringArray::from(vec!["a", "b"]).to_data();
        let score = Float64Array::from(vec![0.5_f64, 0.9]).to_data();
        let (out_a, out_b, out_s) = dedup_pairs_arrow_data_utf8(id_a, id_b, score).unwrap();
        let a = StringArray::from(out_a);
        let b = StringArray::from(out_b);
        let s = Float64Array::from(out_s);
        assert_eq!(a.len(), 1);
        // first-seen intern: "b"=0, "a"=1; canonical (min,max) i64 => (0,1) => ("b","a")
        let pair = (a.value(0), b.value(0));
        assert_eq!(pair, ("b", "a"));
        assert!((s.value(0) - 0.9).abs() < 1e-12);
    }

    #[cfg(feature = "arrow")]
    fn read_list_int64(out: arrow::array::ArrayData) -> Vec<Vec<i64>> {
        let list = ListArray::from(out);
        let mut comps = Vec::new();
        for i in 0..list.len() {
            let vals = list.value(i);
            let int = vals.as_any().downcast_ref::<Int64Array>().unwrap();
            comps.push((0..int.len()).map(|j| int.value(j)).collect());
        }
        comps
    }

    #[cfg(feature = "arrow")]
    fn read_list_utf8(out: arrow::array::ArrayData) -> Vec<Vec<String>> {
        let list = ListArray::from(out);
        let mut comps = Vec::new();
        for i in 0..list.len() {
            let vals = list.value(i);
            let strs = vals.as_any().downcast_ref::<StringArray>().unwrap();
            comps.push((0..strs.len()).map(|j| strs.value(j).to_string()).collect());
        }
        comps
    }

    #[cfg(feature = "arrow")]
    #[test]
    fn cc_arrow_int64_returns_sorted_list() {
        let id_a = Int64Array::from(vec![1_i64, 2]).to_data();
        let id_b = Int64Array::from(vec![2_i64, 3]).to_data();
        let score = Float64Array::from(vec![0.9_f64, 0.8]).to_data();
        let all_ids = Int64Array::from(vec![1_i64, 2, 3, 4]).to_data();
        let out = connected_components_arrow_data(id_a, id_b, score, all_ids).unwrap();
        let mut comps = read_list_int64(out);
        comps.sort();
        assert_eq!(comps, vec![vec![1, 2, 3], vec![4]]);
    }

    #[cfg(feature = "arrow")]
    #[test]
    fn cc_arrow_utf8_returns_sorted_list() {
        let id_a = StringArray::from(vec!["x", "y"]).to_data();
        let id_b = StringArray::from(vec!["y", "z"]).to_data();
        let score = Float64Array::from(vec![0.9_f64, 0.8]).to_data();
        let all_ids = StringArray::from(vec!["x", "y", "z", "w"]).to_data();
        let out = connected_components_arrow_data_utf8(id_a, id_b, score, all_ids).unwrap();
        let mut comps = read_list_utf8(out);
        comps.sort();
        assert_eq!(
            comps,
            vec![
                vec!["w".to_string()],
                vec!["x".to_string(), "y".to_string(), "z".to_string()],
            ]
        );
    }
}
