//! C ABI over `goldenmatch-score-core`: score N string pairs in one call.
//!
//! # Why a buffer ABI and not a JSON one
//!
//! The sibling C ABIs in this repo (`goldenprofile-cabi`, `goldengraph-cabi`)
//! marshal JSON across the boundary, which is right for their request/response
//! shapes. It would be wrong here. This exists so a JVM host can score batches
//! without the per-batch cost it is trying to remove; serializing a batch to
//! JSON to avoid an Arrow IPC hop would trade one marshaling cost for a larger
//! one.
//!
//! So the boundary is **Arrow's string layout**: an `i32` offsets buffer of
//! length `n+1` plus a packed UTF-8 data buffer, exactly as `StringArray` /
//! Spark's UTF8 columnar vectors already hold. A host that has an Arrow buffer
//! (or an off-heap `MemorySegment` / `DirectByteBuffer`) passes its address
//! straight in. Nothing is copied and nothing is allocated across the boundary.
//!
//! # Contract
//!
//! - `out` is **caller-allocated**, `n` `f64`s. Nothing crosses the boundary
//!   owned, so there is no free function and no way to leak.
//! - Returns `0` on success, a negative [`error`] code otherwise. On error
//!   `out` is untouched.
//! - Slot `i` reads `a_data[a_offsets[i]..a_offsets[i+1]]`. Offsets must be
//!   non-decreasing and within the data buffer; they are validated, because
//!   this ABI's whole purpose is to be handed pointers by a JIT-compiled host
//!   and a malformed offset would otherwise be an out-of-bounds read.
//!
//! # Nulls are deliberately NOT handled here
//!
//! There is no validity bitmap parameter. Null semantics are a *policy*
//! decision that belongs to the caller, and this project has already had to fix
//! the consequence of putting it anywhere else: the Python path substituted
//! `""` for a missing value, so null-vs-null scored a perfect 1.0 and two
//! records whose only shared evidence was a shared absence merged at every
//! threshold. The host decides comparability from its own validity bitmaps and
//! never asks this function about a pair it considers unobserved.
//!
//! # Scorer ids
//!
//! Passed straight to `score_one`, which owns ids `0..=14`. The Spark tier
//! currently permits four of them; that is a restriction of its Python config
//! surface, not of this kernel.

use goldenmatch_score_core::score_one;

/// Error codes. Negative so a caller can test `rc < 0` without a table.
pub mod error {
    /// A required pointer was NULL.
    pub const NULL_POINTER: i32 = -1;
    /// `n` was negative, or `n` exceeded what the platform can index.
    pub const BAD_LENGTH: i32 = -2;
    /// Offsets were decreasing, negative, or ran past the data buffer.
    pub const BAD_OFFSETS: i32 = -3;
    /// A slice was not valid UTF-8.
    pub const INVALID_UTF8: i32 = -4;
}

/// Read slot `i` out of an Arrow-layout (offsets, data) pair.
///
/// # Safety
/// `offsets` must be valid for `n + 1` reads and `data` for
/// `offsets[n]` bytes.
#[inline]
unsafe fn slice_at<'a>(
    offsets: *const i32,
    data: *const u8,
    i: usize,
    data_len: i64,
) -> Result<&'a str, i32> {
    let start = *offsets.add(i);
    let end = *offsets.add(i + 1);
    if start < 0 || end < start || i64::from(end) > data_len {
        return Err(error::BAD_OFFSETS);
    }
    let len = (end - start) as usize;
    let bytes = std::slice::from_raw_parts(data.add(start as usize), len);
    std::str::from_utf8(bytes).map_err(|_| error::INVALID_UTF8)
}

/// Score `n` pairs elementwise into `out`.
///
/// Returns `0` on success or a negative [`error`] code. See the module docs for
/// the buffer layout and the deliberate absence of null handling.
///
/// # Safety
/// All five pointers must be non-NULL and valid for the described extents for
/// the duration of the call, and `out` must be writable for `n` `f64`s. The
/// buffers must not alias `out`.
#[no_mangle]
pub unsafe extern "C" fn goldenmatch_score_pairwise_utf8(
    scorer_id: u8,
    a_offsets: *const i32,
    a_data: *const u8,
    a_data_len: i64,
    b_offsets: *const i32,
    b_data: *const u8,
    b_data_len: i64,
    n: i64,
    out: *mut f64,
) -> i32 {
    if a_offsets.is_null()
        || a_data.is_null()
        || b_offsets.is_null()
        || b_data.is_null()
        || out.is_null()
    {
        return error::NULL_POINTER;
    }
    if n < 0 || a_data_len < 0 || b_data_len < 0 {
        return error::BAD_LENGTH;
    }
    let n_usize = match usize::try_from(n) {
        Ok(v) => v,
        Err(_) => return error::BAD_LENGTH,
    };

    // Validate and score in one pass. A separate validation pass would read
    // every offset twice for no benefit; on error `out` is left untouched
    // because nothing has been written past the failing slot -- and a caller
    // that ignores the return code gets stale memory rather than a wrong score
    // it might trust. (The contract says check the code.)
    for i in 0..n_usize {
        let a = match slice_at(a_offsets, a_data, i, a_data_len) {
            Ok(s) => s,
            Err(code) => return code,
        };
        let b = match slice_at(b_offsets, b_data, i, b_data_len) {
            Ok(s) => s,
            Err(code) => return code,
        };
        *out.add(i) = score_one(scorer_id, a, b);
    }
    0
}

/// The number of scorer ids `goldenmatch_score_pairwise_utf8` dispatches.
///
/// Exposed so a host can fail at load time on a version skew rather than
/// silently scoring an unknown id as 0.0 -- the "a new kernel symbol exists but
/// the caller is on an older build" trap this repo has already paid for once.
#[no_mangle]
pub extern "C" fn goldenmatch_score_scorer_id_count() -> u32 {
    15
}

/// ABI version. Bump on any incompatible change to a signature or to the buffer
/// contract, so a host can refuse a mismatched library instead of reading it
/// wrongly.
#[no_mangle]
pub extern "C" fn goldenmatch_score_abi_version() -> u32 {
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build Arrow-layout (offsets, data) from strings.
    fn arrow(values: &[&str]) -> (Vec<i32>, Vec<u8>) {
        let mut offsets = Vec::with_capacity(values.len() + 1);
        let mut data = Vec::new();
        offsets.push(0i32);
        for v in values {
            data.extend_from_slice(v.as_bytes());
            offsets.push(data.len() as i32);
        }
        (offsets, data)
    }

    fn score(scorer_id: u8, a: &[&str], b: &[&str]) -> Result<Vec<f64>, i32> {
        let (ao, ad) = arrow(a);
        let (bo, bd) = arrow(b);
        let mut out = vec![0.0f64; a.len()];
        let rc = unsafe {
            goldenmatch_score_pairwise_utf8(
                scorer_id,
                ao.as_ptr(),
                ad.as_ptr(),
                ad.len() as i64,
                bo.as_ptr(),
                bd.as_ptr(),
                bd.len() as i64,
                a.len() as i64,
                out.as_mut_ptr(),
            )
        };
        if rc == 0 {
            Ok(out)
        } else {
            Err(rc)
        }
    }

    #[test]
    fn matches_score_one_exactly() {
        // The parity claim: this crate marshals, it does not compute. Any
        // divergence from `score_one` means the marshaling is wrong.
        let a = ["jonathan", "smith", "", "ann marie", "Zoë"];
        let b = ["jonothan", "smyth", "", "marie ann", "Zoe"];
        for scorer_id in 0u8..=8 {
            let got = score(scorer_id, &a, &b).expect("scoring failed");
            for (i, g) in got.iter().enumerate() {
                let want = score_one(scorer_id, a[i], b[i]);
                assert_eq!(
                    *g, want,
                    "scorer {scorer_id} slot {i} ({:?} vs {:?})",
                    a[i], b[i]
                );
            }
        }
    }

    #[test]
    fn empty_batch_is_a_no_op_success() {
        assert_eq!(score(0, &[], &[]), Ok(vec![]));
    }

    #[test]
    fn multibyte_offsets_are_byte_offsets_not_char_offsets() {
        // The classic Arrow marshaling bug: treating offsets as character
        // indices truncates every multi-byte value and scores garbage.
        let got = score(0, &["Zoë"], &["Zoë"]).expect("scoring failed");
        assert_eq!(got[0], 1.0, "identical multi-byte strings must score 1.0");
    }

    #[test]
    fn null_pointers_are_refused_not_dereferenced() {
        let (ao, ad) = arrow(&["x"]);
        let mut out = [0.0f64; 1];
        let rc = unsafe {
            goldenmatch_score_pairwise_utf8(
                0,
                ao.as_ptr(),
                ad.as_ptr(),
                ad.len() as i64,
                std::ptr::null(),
                ad.as_ptr(),
                ad.len() as i64,
                1,
                out.as_mut_ptr(),
            )
        };
        assert_eq!(rc, error::NULL_POINTER);
    }

    #[test]
    fn offsets_past_the_data_buffer_are_refused() {
        // A host that miscomputes a slice would otherwise read out of bounds.
        let offsets = [0i32, 99];
        let data = b"ab";
        let mut out = [0.0f64; 1];
        let rc = unsafe {
            goldenmatch_score_pairwise_utf8(
                0,
                offsets.as_ptr(),
                data.as_ptr(),
                data.len() as i64,
                offsets.as_ptr(),
                data.as_ptr(),
                data.len() as i64,
                1,
                out.as_mut_ptr(),
            )
        };
        assert_eq!(rc, error::BAD_OFFSETS);
    }

    #[test]
    fn decreasing_offsets_are_refused() {
        let offsets = [5i32, 2];
        let data = b"abcdef";
        let mut out = [0.0f64; 1];
        let rc = unsafe {
            goldenmatch_score_pairwise_utf8(
                0,
                offsets.as_ptr(),
                data.as_ptr(),
                data.len() as i64,
                offsets.as_ptr(),
                data.as_ptr(),
                data.len() as i64,
                1,
                out.as_mut_ptr(),
            )
        };
        assert_eq!(rc, error::BAD_OFFSETS);
    }

    #[test]
    fn invalid_utf8_is_refused_rather_than_scored() {
        let offsets = [0i32, 2];
        let data = [0xffu8, 0xfe];
        let mut out = [0.0f64; 1];
        let rc = unsafe {
            goldenmatch_score_pairwise_utf8(
                0,
                offsets.as_ptr(),
                data.as_ptr(),
                data.len() as i64,
                offsets.as_ptr(),
                data.as_ptr(),
                data.len() as i64,
                1,
                out.as_mut_ptr(),
            )
        };
        assert_eq!(rc, error::INVALID_UTF8);
    }

    #[test]
    fn negative_length_is_refused() {
        let (ao, ad) = arrow(&["x"]);
        let mut out = [0.0f64; 1];
        let rc = unsafe {
            goldenmatch_score_pairwise_utf8(
                0,
                ao.as_ptr(),
                ad.as_ptr(),
                ad.len() as i64,
                ao.as_ptr(),
                ad.as_ptr(),
                ad.len() as i64,
                -1,
                out.as_mut_ptr(),
            )
        };
        assert_eq!(rc, error::BAD_LENGTH);
    }

    #[test]
    fn abi_version_and_scorer_count_are_reported() {
        assert_eq!(goldenmatch_score_abi_version(), 1);
        // score_one owns 0..=14. If that namespace grows, this must grow with
        // it -- a host gates on this to refuse a skewed library.
        assert_eq!(goldenmatch_score_scorer_id_count(), 15);
    }
}
