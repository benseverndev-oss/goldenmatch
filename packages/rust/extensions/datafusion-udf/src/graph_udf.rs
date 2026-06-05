// Set-consuming graph kernels exposed as datafusion-ffi ScalarUDFs over Arrow
// `List` columns. Unlike the per-pair string scorers (which map row-by-row), a
// graph kernel consumes the WHOLE edge set to produce components, so each UDF
// takes its columns as `List` arguments (the SQL caller aggregates with
// `array_agg`) and returns a `List`. One row in -> one list-of-results out.
//
// These delegate to the SAME pyo3-free `goldenmatch-graph-core` slice kernels
// the DuckDB + Postgres surfaces use (one source of truth):
//   connected_components(edges: &[(i64,i64,f64)], all_ids: &[i64]) -> Vec<Vec<i64>>
//   dedup_pairs_max_score(pairs: &[(i64,i64,f64)])               -> Vec<(i64,i64,f64)>
//
// ARROW-VERSION NOTE: graph-core is built against arrow 55; this crate against
// arrow 58. We deliberately call ONLY the arrow-free SLICE kernels above (which
// take/return plain `Vec`s of i64/f64). We extract i64/f64 out of THIS crate's
// arrow-58 arrays into Vecs, call the slice kernel, and build arrow-58 output.
// No arrow type crosses the 58<->55 boundary, so the mismatch is irrelevant.
//
// SCOPE: int64 ids only. String-id (`_str`) variants are a follow-up task — the
// graph-core first-seen `Dict` exists for an accept-both future.
//
// FFI/pyclass boilerplate (the cr"datafusion_scalar_udf" capsule, the
// FFI_ScalarUDF::from(Arc::new(ScalarUDF::from(self.clone()))) chain) mirrors
// scalar_udf.rs / embed_udf.rs verbatim.

use std::any::Any;
use std::sync::Arc;

use arrow_array::builder::{Float64Builder, Int64Builder, ListBuilder, StructBuilder};
use arrow_array::cast::AsArray;
use arrow_array::Array;
use arrow_schema::{DataType, Field, Fields};
use datafusion_common::error::Result as DataFusionResult;
use datafusion_expr::{
    ColumnarValue, ScalarFunctionArgs, ScalarUDF, ScalarUDFImpl, Signature, Volatility,
};
use datafusion_ffi::udf::FFI_ScalarUDF;
use goldenmatch_graph_core::{connected_components, dedup_pairs_max_score};
use pyo3::types::PyCapsule;
use pyo3::{pyclass, pymethods, Bound, PyResult, Python};

/// `List<Int64>` field shape used for every `List`-of-int64 argument and for the
/// inner element of the components output. Item nullable=true matches the
/// `Int64Builder` default (values are never actually null — we skip nulls on the
/// way in and append full lists on the way out).
fn int64_item_field() -> Arc<Field> {
    Arc::new(Field::new("item", DataType::Int64, true))
}

/// `List<Float64>` field shape for the score argument.
fn float64_item_field() -> Arc<Field> {
    Arc::new(Field::new("item", DataType::Float64, true))
}

/// Extract the `List` element at row `i` as a `Vec<i64>`, skipping nulls. The
/// caller has already downcast the column to a `ListArray<i32>`.
fn list_row_i64(list: &arrow_array::ListArray, i: usize) -> Vec<i64> {
    if list.is_null(i) {
        return Vec::new();
    }
    let vals = list.value(i);
    let arr = vals.as_primitive::<arrow_array::types::Int64Type>();
    let mut out = Vec::with_capacity(arr.len());
    for j in 0..arr.len() {
        if !arr.is_null(j) {
            out.push(arr.value(j));
        }
    }
    out
}

/// Extract the `List<Float64>` element at row `i` as a `Vec<f64>`, skipping
/// nulls.
fn list_row_f64(list: &arrow_array::ListArray, i: usize) -> Vec<f64> {
    if list.is_null(i) {
        return Vec::new();
    }
    let vals = list.value(i);
    let arr = vals.as_primitive::<arrow_array::types::Float64Type>();
    let mut out = Vec::with_capacity(arr.len());
    for j in 0..arr.len() {
        if !arr.is_null(j) {
            out.push(arr.value(j));
        }
    }
    out
}

/// Zip three same-length lists (id_a, id_b, score) into `Vec<(i64,i64,f64)>`
/// edges. Truncates to the shortest if a caller passes ragged lists (a
/// malformed call); the kernels are robust to whatever edge set results.
fn zip_edges(ia: &[i64], ib: &[i64], s: &[f64]) -> Vec<(i64, i64, f64)> {
    let n = ia.len().min(ib.len()).min(s.len());
    (0..n).map(|k| (ia[k], ib[k], s[k])).collect()
}

// ─────────────────────────────────────────────────────────────────────────────
// goldenmatch_connected_components(ia List<Int64>, ib List<Int64>,
//                                  s List<Float64>, ids List<Int64>)
//   -> List<List<Int64>>
// ─────────────────────────────────────────────────────────────────────────────

#[pyclass(
    from_py_object,
    name = "ConnectedComponentsUDF",
    module = "goldenmatch_datafusion_udf",
    subclass
)]
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) struct ConnectedComponentsUDF {
    signature: Signature,
}

#[pymethods]
impl ConnectedComponentsUDF {
    #[new]
    fn new() -> Self {
        Self {
            signature: Signature::exact(
                vec![
                    DataType::List(int64_item_field()),   // id_a
                    DataType::List(int64_item_field()),   // id_b
                    DataType::List(float64_item_field()), // score
                    DataType::List(int64_item_field()),   // all_ids
                ],
                Volatility::Immutable,
            ),
        }
    }

    fn __datafusion_scalar_udf__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyCapsule>> {
        let name = cr"datafusion_scalar_udf".into();
        let func = Arc::new(ScalarUDF::from(self.clone()));
        let provider = FFI_ScalarUDF::from(func);
        PyCapsule::new(py, provider, Some(name))
    }
}

impl ScalarUDFImpl for ConnectedComponentsUDF {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn name(&self) -> &str {
        "goldenmatch_connected_components"
    }

    fn signature(&self) -> &Signature {
        &self.signature
    }

    fn return_type(&self, _arg_types: &[DataType]) -> DataFusionResult<DataType> {
        // List of lists of int64. Both list levels carry the nullable "item"
        // field convention that ListBuilder produces, so DataFusion's exact
        // type validation against the returned array passes.
        Ok(DataType::List(Arc::new(Field::new(
            "item",
            DataType::List(int64_item_field()),
            true,
        ))))
    }

    fn invoke_with_args(&self, args: ScalarFunctionArgs) -> DataFusionResult<ColumnarValue> {
        let arrs = ColumnarValue::values_to_arrays(&args.args)?;
        let ia = arrs[0].as_list::<i32>();
        let ib = arrs[1].as_list::<i32>();
        let sc = arrs[2].as_list::<i32>();
        let ids = arrs[3].as_list::<i32>();

        let n_rows = ia.len();
        // Outer builder: List<List<Int64>>. values() is the inner
        // ListBuilder<Int64Builder>; calling .values() on THAT yields the
        // Int64Builder for individual members.
        let mut outer: ListBuilder<ListBuilder<Int64Builder>> =
            ListBuilder::new(ListBuilder::new(Int64Builder::new()));

        for i in 0..n_rows {
            let edge_a = list_row_i64(ia, i);
            let edge_b = list_row_i64(ib, i);
            let edge_s = list_row_f64(sc, i);
            let all_ids = list_row_i64(ids, i);

            let edges = zip_edges(&edge_a, &edge_b, &edge_s);
            let mut comps = connected_components(&edges, &all_ids);

            // Determinism: sort each component ascending, then sort components
            // by their (now-sorted) contents. The kernel groups by hashmap so
            // order is otherwise nondeterministic across runs.
            for c in comps.iter_mut() {
                c.sort_unstable();
            }
            comps.sort_unstable();

            let inner = outer.values();
            for comp in &comps {
                for &member in comp {
                    inner.values().append_value(member);
                }
                inner.append(true); // close one component (inner list)
            }
            outer.append(true); // close this row's list-of-components
        }

        Ok(ColumnarValue::Array(Arc::new(outer.finish())))
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// goldenmatch_pair_dedup(ia List<Int64>, ib List<Int64>, s List<Float64>)
//   -> List<Struct<a:Int64, b:Int64, s:Float64>>
//
// Canonicalizes each pair (min,max) and keeps the max score per pair. One row
// in -> one list of {a,b,s} structs out.
// ─────────────────────────────────────────────────────────────────────────────

/// The `Struct<a:Int64, b:Int64, s:Float64>` element fields. Shared by the
/// return-type declaration and the StructBuilder construction so the two cannot
/// drift (DataFusion validates the returned array's type against return_type
/// exactly).
fn dedup_struct_fields() -> Fields {
    Fields::from(vec![
        Field::new("a", DataType::Int64, false),
        Field::new("b", DataType::Int64, false),
        Field::new("s", DataType::Float64, false),
    ])
}

#[pyclass(
    from_py_object,
    name = "PairDedupUDF",
    module = "goldenmatch_datafusion_udf",
    subclass
)]
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) struct PairDedupUDF {
    signature: Signature,
}

#[pymethods]
impl PairDedupUDF {
    #[new]
    fn new() -> Self {
        Self {
            signature: Signature::exact(
                vec![
                    DataType::List(int64_item_field()),   // id_a
                    DataType::List(int64_item_field()),   // id_b
                    DataType::List(float64_item_field()), // score
                ],
                Volatility::Immutable,
            ),
        }
    }

    fn __datafusion_scalar_udf__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyCapsule>> {
        let name = cr"datafusion_scalar_udf".into();
        let func = Arc::new(ScalarUDF::from(self.clone()));
        let provider = FFI_ScalarUDF::from(func);
        PyCapsule::new(py, provider, Some(name))
    }
}

impl ScalarUDFImpl for PairDedupUDF {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn name(&self) -> &str {
        "goldenmatch_pair_dedup"
    }

    fn signature(&self) -> &Signature {
        &self.signature
    }

    fn return_type(&self, _arg_types: &[DataType]) -> DataFusionResult<DataType> {
        Ok(DataType::List(Arc::new(Field::new(
            "item",
            DataType::Struct(dedup_struct_fields()),
            true,
        ))))
    }

    fn invoke_with_args(&self, args: ScalarFunctionArgs) -> DataFusionResult<ColumnarValue> {
        let arrs = ColumnarValue::values_to_arrays(&args.args)?;
        let ia = arrs[0].as_list::<i32>();
        let ib = arrs[1].as_list::<i32>();
        let sc = arrs[2].as_list::<i32>();

        let n_rows = ia.len();
        let fields = dedup_struct_fields();
        // ListBuilder over a StructBuilder. StructBuilder::from_fields builds the
        // child builders (Int64Builder, Int64Builder, Float64Builder) in field
        // order; we index them by position when appending.
        let struct_builder = StructBuilder::from_fields(fields.clone(), 0);
        let mut outer: ListBuilder<StructBuilder> = ListBuilder::new(struct_builder);

        for i in 0..n_rows {
            let pa = list_row_i64(ia, i);
            let pb = list_row_i64(ib, i);
            let ps = list_row_f64(sc, i);
            let pairs = zip_edges(&pa, &pb, &ps);

            let mut deduped = dedup_pairs_max_score(&pairs);
            // dedup_pairs_max_score already returns sorted by (a,b) via the
            // BTreeMap, but sort defensively for a stable contract.
            deduped.sort_by(|x, y| {
                x.0.cmp(&y.0)
                    .then(x.1.cmp(&y.1))
                    .then(x.2.partial_cmp(&y.2).unwrap_or(std::cmp::Ordering::Equal))
            });

            let sb = outer.values();
            for (a, b, s) in &deduped {
                sb.field_builder::<Int64Builder>(0)
                    .expect("field 0 is Int64Builder")
                    .append_value(*a);
                sb.field_builder::<Int64Builder>(1)
                    .expect("field 1 is Int64Builder")
                    .append_value(*b);
                sb.field_builder::<Float64Builder>(2)
                    .expect("field 2 is Float64Builder")
                    .append_value(*s);
                // Mark this struct row valid AFTER its child fields are filled.
                sb.append(true);
            }
            outer.append(true); // close this row's list-of-structs
        }

        Ok(ColumnarValue::Array(Arc::new(outer.finish())))
    }
}
