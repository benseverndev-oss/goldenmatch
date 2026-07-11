//! Arrow-native decoding helpers shared by the kernels: column interning to
//! dense `u64` value-ids (for the key/FD kernels) and typed numeric extraction
//! (for Benford). Moved down from the `goldencheck-native` pyo3 shim so the
//! Arrow boundary lives in the pyo3-free core. No pyo3, no Python.
use arrow::array::{
    Array, BooleanArray, DictionaryArray, Float32Array, Float64Array, Int16Array, Int32Array,
    Int64Array, Int8Array, LargeStringArray, StringArray, UInt16Array, UInt32Array, UInt64Array,
    UInt8Array,
};
use arrow::datatypes::{
    ArrowNativeType, DataType, Int16Type, Int32Type, Int64Type, Int8Type, UInt16Type, UInt32Type,
    UInt64Type, UInt8Type,
};
use arrow::error::ArrowError;
use rustc_hash::FxHashMap;

/// Intern one Arrow column to dense `u64` value-ids. Null slots all share a
/// single reserved id (0) distinct from every real value's id; non-null values
/// get dense ids 1,2,3,... in first-seen order. Floats are keyed by bit pattern
/// (exact value identity); booleans map true->1, false->2, null->0. Returns an
/// `ArrowError` for dtypes the key/FD kernels don't support.
///
/// Takes a borrowed `&dyn Array` and downcasts to the concrete typed array,
/// keeping the caller's data untouched (byte-identical semantics to the
/// previous owned-`ArrayData` native shim, so interned ids stay stable).
pub fn intern_column(array: &dyn Array) -> Result<Vec<u64>, ArrowError> {
    macro_rules! intern_primitive {
        ($arrty:ty, $keyty:ty, $keyexpr:expr) => {{
            let arr = array.as_any().downcast_ref::<$arrty>().unwrap();
            let mut map: FxHashMap<$keyty, u64> = FxHashMap::default();
            let mut ids = Vec::with_capacity(arr.len());
            let mut next: u64 = 1; // 0 is reserved for null
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    ids.push(0);
                    continue;
                }
                let key: $keyty = $keyexpr(arr, i);
                let id = *map.entry(key).or_insert_with(|| {
                    let v = next;
                    next += 1;
                    v
                });
                ids.push(id);
            }
            Ok(ids)
        }};
    }

    match array.data_type() {
        DataType::Utf8 => {
            let arr = array.as_any().downcast_ref::<StringArray>().unwrap();
            let mut map: FxHashMap<&str, u64> = FxHashMap::default();
            let mut ids = Vec::with_capacity(arr.len());
            let mut next: u64 = 1;
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    ids.push(0);
                    continue;
                }
                let id = *map.entry(arr.value(i)).or_insert_with(|| {
                    let v = next;
                    next += 1;
                    v
                });
                ids.push(id);
            }
            Ok(ids)
        }
        DataType::LargeUtf8 => {
            let arr = array.as_any().downcast_ref::<LargeStringArray>().unwrap();
            let mut map: FxHashMap<&str, u64> = FxHashMap::default();
            let mut ids = Vec::with_capacity(arr.len());
            let mut next: u64 = 1;
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    ids.push(0);
                    continue;
                }
                let id = *map.entry(arr.value(i)).or_insert_with(|| {
                    let v = next;
                    next += 1;
                    v
                });
                ids.push(id);
            }
            Ok(ids)
        }
        DataType::Int8 => intern_primitive!(Int8Array, i8, |a: &Int8Array, i| a.value(i)),
        DataType::Int16 => intern_primitive!(Int16Array, i16, |a: &Int16Array, i| a.value(i)),
        DataType::Int32 => intern_primitive!(Int32Array, i32, |a: &Int32Array, i| a.value(i)),
        DataType::Int64 => intern_primitive!(Int64Array, i64, |a: &Int64Array, i| a.value(i)),
        DataType::UInt8 => intern_primitive!(UInt8Array, u8, |a: &UInt8Array, i| a.value(i)),
        DataType::UInt16 => intern_primitive!(UInt16Array, u16, |a: &UInt16Array, i| a.value(i)),
        DataType::UInt32 => intern_primitive!(UInt32Array, u32, |a: &UInt32Array, i| a.value(i)),
        DataType::UInt64 => intern_primitive!(UInt64Array, u64, |a: &UInt64Array, i| a.value(i)),
        // Floats keyed by bit pattern (exact value identity; matches Polars
        // equality semantics for finite values, which is all a key column has).
        DataType::Float32 => {
            intern_primitive!(Float32Array, u32, |a: &Float32Array, i| a
                .value(i)
                .to_bits())
        }
        DataType::Float64 => {
            intern_primitive!(Float64Array, u64, |a: &Float64Array, i| a
                .value(i)
                .to_bits())
        }
        DataType::Boolean => {
            let arr = array.as_any().downcast_ref::<BooleanArray>().unwrap();
            let mut ids = Vec::with_capacity(arr.len());
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    ids.push(0);
                } else {
                    ids.push(if arr.value(i) { 1 } else { 2 });
                }
            }
            Ok(ids)
        }
        // Dictionary-encoded (e.g. a Polars Categorical/Enum column). The
        // dictionary *is* the interning: intern the (small) values array ONCE,
        // then map each row's key through it -- O(dict) hashing + O(rows) index
        // lookups, instead of O(rows) hashing on the raw path. A null row keeps
        // id 0.
        DataType::Dictionary(key_type, _) => {
            macro_rules! intern_dict {
                ($kt:ty) => {{
                    let dict = array
                        .as_any()
                        .downcast_ref::<DictionaryArray<$kt>>()
                        .unwrap();
                    // Dense id per dictionary entry (recurses on the small values
                    // array; null entries -> 0, values -> 1,2,... first-seen).
                    let value_ids = intern_column(dict.values().as_ref())?;
                    let keys = dict.keys();
                    let mut ids = Vec::with_capacity(keys.len());
                    for i in 0..keys.len() {
                        if keys.is_null(i) {
                            ids.push(0);
                        } else {
                            ids.push(value_ids[keys.value(i).as_usize()]);
                        }
                    }
                    Ok(ids)
                }};
            }
            match key_type.as_ref() {
                DataType::Int8 => intern_dict!(Int8Type),
                DataType::Int16 => intern_dict!(Int16Type),
                DataType::Int32 => intern_dict!(Int32Type),
                DataType::Int64 => intern_dict!(Int64Type),
                DataType::UInt8 => intern_dict!(UInt8Type),
                DataType::UInt16 => intern_dict!(UInt16Type),
                DataType::UInt32 => intern_dict!(UInt32Type),
                DataType::UInt64 => intern_dict!(UInt64Type),
                other => Err(ArrowError::InvalidArgumentError(format!(
                    "key/FD kernels: unsupported dictionary key type {other:?}"
                ))),
            }
        }
        other => Err(ArrowError::InvalidArgumentError(format!(
            "key/FD kernels do not support Arrow dtype {other:?}; \
             cast to string/int/float/bool first"
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{Float64Array, Int64Array, StringArray};
    use std::sync::Arc;

    #[test]
    fn interns_strings_dense_from_one_nulls_zero() {
        let a = StringArray::from(vec![Some("x"), Some("y"), Some("x"), None]);
        let ids = intern_column(&a).unwrap();
        assert_eq!(ids[0], ids[2]); // same value -> same id
        assert_ne!(ids[0], ids[1]); // different value -> different id
        assert_eq!(ids[3], 0); // null -> reserved 0
        assert!(ids[0] >= 1 && ids[1] >= 1);
    }

    #[test]
    fn interns_ints_and_floats_by_value() {
        let a = Int64Array::from(vec![Some(5), Some(5), Some(6), None]);
        let ids = intern_column(&a).unwrap();
        assert_eq!(ids[0], ids[1]);
        assert_ne!(ids[0], ids[2]);
        assert_eq!(ids[3], 0);
        let f = Float64Array::from(vec![Some(1.5), Some(1.5), Some(2.5)]);
        let fids = intern_column(&f).unwrap();
        assert_eq!(fids[0], fids[1]);
        assert_ne!(fids[0], fids[2]);
    }

    #[test]
    fn unsupported_dtype_errors() {
        use arrow::array::Date32Array;
        let a = Date32Array::from(vec![1, 2, 3]);
        assert!(intern_column(&a).is_err());
    }

    #[test]
    fn arc_dyn_array_accepted() {
        let a: Arc<dyn Array> = Arc::new(StringArray::from(vec!["a", "b"]));
        assert_eq!(intern_column(a.as_ref()).unwrap().len(), 2);
    }

    #[test]
    fn dictionary_encoded_interns_like_plain_string() {
        use arrow::array::{DictionaryArray, Int32Array};
        use arrow::datatypes::Int32Type;

        let plain = StringArray::from(vec![Some("x"), Some("y"), Some("x"), None]);
        let plain_ids = intern_column(&plain).unwrap();

        // Same logical values, dictionary-encoded: dict = [x, y], keys = [0,1,0,null].
        let dict: DictionaryArray<Int32Type> = vec![Some("x"), Some("y"), Some("x"), None]
            .into_iter()
            .collect();
        let dict_ids = intern_column(&dict).unwrap();
        assert_eq!(dict_ids, plain_ids);

        // Sanity: raw keys/values shape is as expected.
        let keys: Int32Array = dict.keys().clone();
        assert_eq!(keys.len(), 4);
    }

    #[test]
    fn dictionary_unsupported_key_type_errors() {
        // Values array itself unsupported (Date32) surfaces through the recursive
        // intern_column call on `dict.values()`.
        use arrow::array::{Date32Array, DictionaryArray, Int32Array};
        use arrow::datatypes::Int32Type;
        let values: Date32Array = vec![1, 2].into();
        let keys = Int32Array::from(vec![0, 1, 0]);
        let dict =
            DictionaryArray::<Int32Type>::try_new(keys, std::sync::Arc::new(values)).unwrap();
        assert!(intern_column(&dict).is_err());
    }
}
