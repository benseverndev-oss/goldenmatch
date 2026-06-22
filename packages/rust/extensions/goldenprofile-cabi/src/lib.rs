//! C ABI over `goldenprofile-core`, via the shared `goldenprofile_wasm`
//! marshaling shim. A C (or any FFI) caller passes a JSON `ResolveRequest`
//! string and receives an owned JSON `Resolution` string it must free with
//! [`goldenprofile_string_free`].
//!
//! Contract:
//! - `goldenprofile_resolve_json(req)` returns a freshly-allocated C string on
//!   success, or NULL if `req` is NULL / not valid UTF-8 / a malformed request.
//!   (For a richer error channel, callers can wrap; v1 keeps a single NULL
//!   sentinel.)
//! - Every non-NULL pointer returned MUST be passed back to
//!   `goldenprofile_string_free` exactly once. The strings are heap-allocated by
//!   Rust; freeing them any other way is UB.

use std::ffi::{c_char, CStr, CString};

use goldenprofile_wasm::resolve_json_impl;

/// Resolve a NUL-terminated JSON request into a NUL-terminated JSON result.
/// Returns NULL on a NULL/invalid-UTF-8/malformed-request input. The returned
/// pointer is owned by the caller -- free it with `goldenprofile_string_free`.
///
/// # Safety
/// `request` must be NULL or a valid pointer to a NUL-terminated C string that
/// stays valid for the duration of the call.
#[no_mangle]
pub unsafe extern "C" fn goldenprofile_resolve_json(request: *const c_char) -> *mut c_char {
    if request.is_null() {
        return std::ptr::null_mut();
    }
    let req = match CStr::from_ptr(request).to_str() {
        Ok(s) => s,
        Err(_) => return std::ptr::null_mut(),
    };
    match resolve_json_impl(req) {
        // Result JSON never contains an interior NUL, so this unwrap is safe.
        Ok(out) => CString::new(out)
            .map(CString::into_raw)
            .unwrap_or(std::ptr::null_mut()),
        Err(_) => std::ptr::null_mut(),
    }
}

/// Free a string returned by `goldenprofile_resolve_json`. NULL is a no-op.
///
/// # Safety
/// `s` must be NULL or a pointer previously returned by
/// `goldenprofile_resolve_json` and not yet freed.
#[no_mangle]
pub unsafe extern "C" fn goldenprofile_string_free(s: *mut c_char) {
    if !s.is_null() {
        drop(CString::from_raw(s));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_through_c_abi() {
        let req = CString::new(
            r#"{"profiles":[
                {"kind":"node","name":"Thomas Nabbes","category":"Playwright","anchor":"UNKNOWN","attribute":"Wrote X"},
                {"kind":"node","name":"Nabbes","category":"Playwright","anchor":"UNKNOWN","attribute":"Born 1605"}
            ]}"#,
        )
        .unwrap();
        unsafe {
            let out = goldenprofile_resolve_json(req.as_ptr());
            assert!(!out.is_null());
            let s = CStr::from_ptr(out).to_str().unwrap();
            assert!(s.contains("\"clusters\""));
            // Both Nabbes mentions in one cluster.
            assert!(s.contains("[0,1]"));
            goldenprofile_string_free(out);
        }
    }

    #[test]
    fn null_input_returns_null() {
        unsafe {
            assert!(goldenprofile_resolve_json(std::ptr::null()).is_null());
            goldenprofile_string_free(std::ptr::null_mut()); // no-op, must not crash
        }
    }
}
