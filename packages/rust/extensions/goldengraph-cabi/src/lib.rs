//! C ABI over `goldengraph-core` — the C analogue of `goldengraph-native` (pyo3)
//! and `goldengraph-wasm` (wasm-bindgen). It wraps the SAME `*_impl` fns the WASM
//! crate exposes, so the engine is byte-identical across Python, WASM, and C by
//! construction; this crate only crosses the C boundary.
//!
//! Boundary (SP5): **stateless functions over the canonical snapshot JSON** — no
//! handles/lifetimes cross the boundary, every op is `(json, args...) -> json`.
//! Calling convention (also documented in `include/goldengraph.h`):
//!   * inputs are NUL-terminated UTF-8 JSON C strings;
//!   * the result JSON is written NUL-terminated into `out`;
//!   * the return is the content length in bytes EXCLUDING the NUL;
//!   * **two-call sizing**: call with `out=NULL, out_cap=0` to learn the length,
//!     allocate `len+1`, call again;
//!   * a NEGATIVE return is an error code (see `GG_ERR_*`); the message is on
//!     [`gg_last_error`] (per-thread).

use std::cell::RefCell;
use std::os::raw::c_char;
use std::ffi::CStr;

use goldengraph_wasm::{
    build_graph_impl, communities_impl, neighborhood_impl, seeds_by_name_impl, store_append_impl,
    store_as_of_impl, store_history_impl,
};

/// ABI version of this library — bump on any breaking C-signature change.
pub const GG_ABI_VERSION: u32 = 1;

/// A required pointer argument was NULL.
pub const GG_ERR_NULL_ARG: isize = -1;
/// A string argument was not valid UTF-8.
pub const GG_ERR_BAD_UTF8: isize = -2;
/// The operation failed (bad JSON shape / store error); see `gg_last_error`.
pub const GG_ERR_OP_FAILED: isize = -3;

thread_local! {
    static LAST_ERROR: RefCell<String> = const { RefCell::new(String::new()) };
}

fn set_error(msg: impl Into<String>) {
    LAST_ERROR.with(|e| *e.borrow_mut() = msg.into());
}

/// Read a NUL-terminated C string as `&str`. Maps null/non-UTF-8 to an error code.
///
/// # Safety
/// `p` must be NULL or point to a valid NUL-terminated C string.
unsafe fn read_cstr<'a>(p: *const c_char) -> Result<&'a str, isize> {
    if p.is_null() {
        set_error("null argument");
        return Err(GG_ERR_NULL_ARG);
    }
    CStr::from_ptr(p).to_str().map_err(|_| {
        set_error("argument is not valid UTF-8");
        GG_ERR_BAD_UTF8
    })
}

/// Write `s` into `out`/`out_cap`, NUL-terminated, IF it fits (needs `out_cap >
/// s.len()`). Always returns `s.len()` (excl NUL) so the caller can size a retry.
///
/// # Safety
/// `out` must be valid for `out_cap` bytes, or NULL iff `out_cap == 0`.
unsafe fn write_out(s: &str, out: *mut c_char, out_cap: usize) -> isize {
    let bytes = s.as_bytes();
    let n = bytes.len();
    if !out.is_null() && out_cap > n {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), out as *mut u8, n);
        *out.add(n) = 0; // NUL terminator
    }
    n as isize
}

/// Common tail: an `Ok` result is written out; an `Err` sets the thread error and
/// returns `GG_ERR_OP_FAILED`.
///
/// # Safety
/// `out`/`out_cap` per [`write_out`].
unsafe fn finish(r: Result<String, String>, out: *mut c_char, out_cap: usize) -> isize {
    match r {
        Ok(s) => write_out(&s, out, out_cap),
        Err(msg) => {
            set_error(msg);
            GG_ERR_OP_FAILED
        }
    }
}

/// The ABI version of this shared library.
#[no_mangle]
pub extern "C" fn gg_abi_version() -> u32 {
    GG_ABI_VERSION
}

/// `(mentions_json, edges_json, resolution_json) -> graph_json` (SP1 resolve + merge).
///
/// # Safety
/// String args must be NUL-terminated or NULL; `out` valid for `out_cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn gg_build_graph(
    mentions_json: *const c_char,
    edges_json: *const c_char,
    resolution_json: *const c_char,
    out: *mut c_char,
    out_cap: usize,
) -> isize {
    let m = match read_cstr(mentions_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    let e = match read_cstr(edges_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    let r = match read_cstr(resolution_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    finish(build_graph_impl(m, e, r), out, out_cap)
}

/// `(graph_json, seeds_json, hops) -> subgraph_json` (1-2 hop retrieval).
///
/// # Safety
/// String args must be NUL-terminated or NULL; `out` valid for `out_cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn gg_neighborhood(
    graph_json: *const c_char,
    seeds_json: *const c_char,
    hops: u8,
    out: *mut c_char,
    out_cap: usize,
) -> isize {
    let g = match read_cstr(graph_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    let s = match read_cstr(seeds_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    finish(neighborhood_impl(g, s, hops), out, out_cap)
}

/// `(graph_json, name) -> entity_ids_json` (seed by canonical OR surface name).
///
/// # Safety
/// String args must be NUL-terminated or NULL; `out` valid for `out_cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn gg_seeds_by_name(
    graph_json: *const c_char,
    name: *const c_char,
    out: *mut c_char,
    out_cap: usize,
) -> isize {
    let g = match read_cstr(graph_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    let n = match read_cstr(name) {
        Ok(s) => s,
        Err(c) => return c,
    };
    finish(seeds_by_name_impl(g, n), out, out_cap)
}

/// `(graph_json) -> communities_json` (SP3 label-propagation partition).
///
/// # Safety
/// String args must be NUL-terminated or NULL; `out` valid for `out_cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn gg_communities(
    graph_json: *const c_char,
    out: *mut c_char,
    out_cap: usize,
) -> isize {
    let g = match read_cstr(graph_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    finish(communities_impl(g), out, out_cap)
}

/// `(snapshot_json, batch_json) -> snapshot_json`. An empty `snapshot_json` ("")
/// opens a fresh store (SP2 append + bi-temporal reconciliation).
///
/// # Safety
/// String args must be NUL-terminated or NULL; `out` valid for `out_cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn gg_store_append(
    snapshot_json: *const c_char,
    batch_json: *const c_char,
    out: *mut c_char,
    out_cap: usize,
) -> isize {
    let snap = match read_cstr(snapshot_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    let batch = match read_cstr(batch_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    finish(store_append_impl(snap, batch), out, out_cap)
}

/// `(snapshot_json, valid_t, tx_t) -> graph_json` (SP2 bi-temporal slice).
///
/// # Safety
/// String args must be NUL-terminated or NULL; `out` valid for `out_cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn gg_store_as_of(
    snapshot_json: *const c_char,
    valid_t: i64,
    tx_t: i64,
    out: *mut c_char,
    out_cap: usize,
) -> isize {
    let snap = match read_cstr(snapshot_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    finish(store_as_of_impl(snap, valid_t, tx_t), out, out_cap)
}

/// `(snapshot_json, id) -> history_events_json` (SP2 per-entity event log).
///
/// # Safety
/// String args must be NUL-terminated or NULL; `out` valid for `out_cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn gg_store_history(
    snapshot_json: *const c_char,
    id: u64,
    out: *mut c_char,
    out_cap: usize,
) -> isize {
    let snap = match read_cstr(snapshot_json) {
        Ok(s) => s,
        Err(c) => return c,
    };
    finish(store_history_impl(snap, id), out, out_cap)
}

/// The last error message on the CURRENT thread (set by the most recent failing
/// call), written with the same two-call sizing as the op functions. Empty if no
/// error has occurred on this thread.
///
/// # Safety
/// `out` must be valid for `out_cap` bytes, or NULL iff `out_cap == 0`.
#[no_mangle]
pub unsafe extern "C" fn gg_last_error(out: *mut c_char, out_cap: usize) -> isize {
    LAST_ERROR.with(|e| write_out(&e.borrow(), out, out_cap))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CString;

    const BATCH: &str = r#"{"entities":[
        {"local_id":0,"canonical_name":"Acme","typ":"org","surface_names":["Acme"],"record_keys":["k0"]},
        {"local_id":1,"canonical_name":"Rocket","typ":"product","surface_names":["Rocket"],"record_keys":["k1"]}],
        "edges":[{"subj_local":0,"predicate":"made","obj_local":1,"valid_from":100,"valid_to":null,"source_refs":["doc"]}],
        "ingested_at":100}"#;

    /// Two-call sizing helper: size, allocate, fill — exactly the documented C flow.
    unsafe fn call2(f: impl Fn(*mut c_char, usize) -> isize) -> Result<String, isize> {
        let n = f(std::ptr::null_mut(), 0);
        if n < 0 {
            return Err(n);
        }
        let mut buf = vec![0u8; n as usize + 1];
        let m = f(buf.as_mut_ptr() as *mut c_char, buf.len());
        assert_eq!(m, n, "second call must report the same length");
        assert_eq!(buf[n as usize], 0, "must be NUL-terminated");
        buf.truncate(n as usize);
        Ok(String::from_utf8(buf).unwrap())
    }

    #[test]
    fn abi_version_is_one() {
        assert_eq!(gg_abi_version(), 1);
    }

    #[test]
    fn append_then_as_of_round_trips_through_c() {
        let batch = CString::new(BATCH).unwrap();
        let empty = CString::new("").unwrap();
        let snap = unsafe {
            call2(|o, c| gg_store_append(empty.as_ptr(), batch.as_ptr(), o, c)).unwrap()
        };
        assert!(snap.contains("Acme") && snap.contains("Rocket"));

        let snap_c = CString::new(snap).unwrap();
        let graph = unsafe {
            call2(|o, c| gg_store_as_of(snap_c.as_ptr(), 1_000, 1_000, o, c)).unwrap()
        };
        // the as_of slice carries both entities and the made-edge
        assert!(graph.contains("Acme") && graph.contains("Rocket") && graph.contains("made"));
    }

    #[test]
    fn c_boundary_is_byte_identical_to_the_impl() {
        // The cabi only marshals — confirm nothing is corrupted crossing C.
        let batch = CString::new(BATCH).unwrap();
        let empty = CString::new("").unwrap();
        let via_c =
            unsafe { call2(|o, c| gg_store_append(empty.as_ptr(), batch.as_ptr(), o, c)).unwrap() };
        let direct = store_append_impl("", BATCH).unwrap();
        assert_eq!(via_c, direct);
    }

    #[test]
    fn null_arg_returns_null_code() {
        let batch = CString::new(BATCH).unwrap();
        let mut buf = [0u8; 8];
        let rc = unsafe {
            gg_store_append(std::ptr::null(), batch.as_ptr(), buf.as_mut_ptr() as *mut c_char, 8)
        };
        assert_eq!(rc, GG_ERR_NULL_ARG);
    }

    #[test]
    fn bad_json_sets_last_error() {
        let empty = CString::new("").unwrap();
        let bad = CString::new("{ not valid json").unwrap();
        let mut buf = [0u8; 8];
        let rc = unsafe {
            gg_store_append(empty.as_ptr(), bad.as_ptr(), buf.as_mut_ptr() as *mut c_char, 8)
        };
        assert_eq!(rc, GG_ERR_OP_FAILED);
        let msg = unsafe { call2(|o, c| gg_last_error(o, c)).unwrap() };
        assert!(!msg.is_empty(), "error message should be populated");
    }

    #[test]
    fn too_small_buffer_does_not_write_but_reports_length() {
        let batch = CString::new(BATCH).unwrap();
        let empty = CString::new("").unwrap();
        // out_cap=1 can't hold the snapshot; return the true length, leave buf alone.
        let mut buf = [0xAAu8; 1];
        let n = unsafe {
            gg_store_append(empty.as_ptr(), batch.as_ptr(), buf.as_mut_ptr() as *mut c_char, 1)
        };
        assert!(n > 1, "snapshot is longer than 1 byte");
        assert_eq!(buf[0], 0xAA, "must not write into an undersized buffer");
    }
}
