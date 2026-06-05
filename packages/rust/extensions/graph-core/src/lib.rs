//! Pyo3-free graph kernels. Behavior-exact extraction of the loops that lived in
//! `native/src/{cluster,pairs}.rs`; the `native` crate keeps thin `#[pyfunction]`
//! shims delegating here (one source of truth, like `score-core`).
mod dict;
pub use dict::*;

use std::collections::BTreeMap;
use std::collections::HashMap;

use arrow::array::builder::{Int64Builder, ListBuilder, StringBuilder};
use arrow::array::{Array, ArrayData, Float64Array, Int64Array, ListArray, StringArray};
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

/// Arrow columnar dedup over int64 id columns + float64 score. Validates types,
/// reads the columns, runs `dedup_pairs_max_score`, returns three Arrow arrays.
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

    use arrow::array::{Array, Float64Array, Int64Array, ListArray, StringArray};

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
