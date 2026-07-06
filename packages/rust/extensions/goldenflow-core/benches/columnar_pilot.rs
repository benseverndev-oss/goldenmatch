//! Arrow-columnar kernel PILOT (owned-kernel cutover, pillar 1/3 decision).
//!
//! Question: does turning the scalar-per-element kernel path
//! (`for_each_str` -> `fn(&str)->Option<String>` -> `StringBuilder::append`) into
//! an Arrow-COLUMNAR kernel (operate on the whole StringArray buffer at once)
//! actually pay -- and where? We measure two representative shapes on a 1M-row
//! Utf8 array, 5-run median wall (the repo's convention), asserting the columnar
//! output is byte-identical to the scalar output (so the corpus contract holds):
//!
//!   - `lowercase`  = byte-trivial op. Columnar can lowercase the contiguous
//!     values buffer in one pass (ASCII fast path, SIMD-friendly `make_ascii_
//!     lowercase`), reusing offsets+nulls -> ZERO per-element String allocs.
//!   - `name_proper`    = compute-heavy per-string transform. Cannot vectorize;
//!     "columnar" here is only exact-capacity sizing + no closure/Option
//!     indirection. Expected: near-parity -> the sweep should NOT re-architect
//!     compute-heavy kernels.
//!
//! Run: `cargo bench --bench columnar_pilot` (release/bench profile).

use std::hint::black_box;
use std::time::Instant;

use arrow_array::builder::StringBuilder;
use arrow_array::{Array, StringArray};
use arrow_buffer::{Buffer, OffsetBuffer, ScalarBuffer};

use goldenflow_core::names::name_proper;
use goldenflow_core::text::{lowercase, strip};

const N: usize = 1_000_000;
const RUNS: usize = 5;

// --- The CURRENT shipping shape (mirrors native-flow util::map_str_to_str) -----
// closure returning Option<String>, Some-wrap, generic len*12 value capacity,
// per-element append.

fn scalar_map<F: Fn(&str) -> Option<String>>(arr: &StringArray, f: F) -> StringArray {
    let len = arr.len();
    let mut b = StringBuilder::with_capacity(len, len * 12);
    for v in arr.iter() {
        match v {
            Some(s) => match f(s) {
                Some(out) => b.append_value(out),
                None => b.append_null(),
            },
            None => b.append_null(),
        }
    }
    b.finish()
}

fn scalar_lowercase(arr: &StringArray) -> StringArray {
    scalar_map(arr, |s| Some(lowercase(s)))
}

fn scalar_name_proper(arr: &StringArray) -> StringArray {
    scalar_map(arr, |s| Some(name_proper(s)))
}

fn scalar_strip(arr: &StringArray) -> StringArray {
    scalar_map(arr, |s| Some(strip(s).to_string()))
}

// --- GENERIC columnar map: one primitive for ANY op --------------------------
// `f` writes the transformed bytes of each element directly into ONE shared
// values buffer (no per-element String alloc, no builder double-copy); offsets
// are computed as we go. This is the "one primitive, all trivial ops adapt"
// candidate -- captures the alloc/copy elimination WITHOUT op-specific buffer
// surgery or whole-buffer SIMD.

fn generic_columnar<F: Fn(&str, &mut String)>(arr: &StringArray, f: F) -> StringArray {
    let len = arr.len();
    let mut offsets: Vec<i32> = Vec::with_capacity(len + 1);
    offsets.push(0);
    let mut values = String::with_capacity(arr.values().len());
    for v in arr.iter() {
        if let Some(s) = v {
            f(s, &mut values);
        }
        offsets.push(values.len() as i32);
    }
    StringArray::new(
        OffsetBuffer::new(ScalarBuffer::from(offsets)),
        Buffer::from_vec(values.into_bytes()),
        arr.nulls().cloned(),
    )
}

fn generic_lowercase(arr: &StringArray) -> StringArray {
    // ASCII case-fold appended byte-wise (matches make_ascii_lowercase on ASCII;
    // non-ASCII bytes are >= 0x80 and unchanged, so the buffer stays valid UTF-8).
    generic_columnar(arr, |s, buf| {
        // SAFETY: ascii-lowercasing UTF-8 bytes yields valid UTF-8.
        let v = unsafe { buf.as_mut_vec() };
        v.extend(s.as_bytes().iter().map(|b| b.to_ascii_lowercase()));
    })
}

fn generic_strip(arr: &StringArray) -> StringArray {
    // trim() is zero-alloc slicing; push_str copies the slice once -> no
    // per-element String allocation (unlike scalar `strip(s)`).
    generic_columnar(arr, |s, buf| buf.push_str(strip(s)))
}

// --- The COLUMNAR variants -----------------------------------------------------

/// Buffer-level lowercase. When the whole values buffer is ASCII, lowercase it
/// in a single `make_ascii_lowercase` pass (byte lengths unchanged, so offsets +
/// nulls are reused verbatim) -- no per-element allocation. Falls back to the
/// scalar Unicode path if any non-ASCII byte is present (correctness first:
/// `make_ascii_lowercase` != `to_lowercase` outside ASCII).
fn columnar_lowercase(arr: &StringArray) -> StringArray {
    let bytes: &[u8] = arr.values().as_slice();
    if !bytes.is_ascii() {
        return scalar_lowercase(arr);
    }
    let mut v = bytes.to_vec();
    v.make_ascii_lowercase();
    StringArray::new(arr.offsets().clone(), Buffer::from_vec(v), arr.nulls().cloned())
}

/// "Columnar" name_proper: no closure / Option indirection, capacity-hinted.
/// The title-case compute is inherently per-string (unchanged).
fn columnar_name_proper(arr: &StringArray) -> StringArray {
    let len = arr.len();
    let mut b = StringBuilder::with_capacity(len, len * 16);
    for v in arr.iter() {
        match v {
            Some(s) => b.append_value(name_proper(s)),
            None => b.append_null(),
        }
    }
    b.finish()
}

// --- Harness -------------------------------------------------------------------

fn bench<F: Fn() -> StringArray>(f: F) -> (f64, StringArray) {
    let out = f(); // warmup + captured for the parity assert
    let mut times = Vec::with_capacity(RUNS);
    for _ in 0..RUNS {
        let t = Instant::now();
        let r = f();
        times.push(t.elapsed().as_secs_f64() * 1000.0);
        black_box(&r);
    }
    times.sort_by(|a, b| a.partial_cmp(b).unwrap());
    (times[RUNS / 2], out)
}

fn assert_same(a: &StringArray, b: &StringArray) {
    assert_eq!(a.len(), b.len(), "length mismatch");
    for i in 0..a.len() {
        assert_eq!(a.is_null(i), b.is_null(i), "null mismatch at {i}");
        if !a.is_null(i) {
            assert_eq!(a.value(i), b.value(i), "value mismatch at {i}");
        }
    }
}

fn build_data() -> StringArray {
    // All-ASCII, realistic messy mixed-case name-like strings (the common ER
    // case; non-ASCII names are typically transliterated upstream). ~1% nulls.
    let pool = [
        "John Smith", "  MARY Jones  ", "o'Brien", "MacDonald", "Robert",
        "Rupert", "Ashcraft", "Tymczak", "Van Der Berg", "de la CRUZ",
        "JACKSON", "Washington", "Honeyman", "Gauss", "Pfister",
        "Catherine", "Katherine", "Thompson", "Ghislaine", "Wright",
    ];
    let owned: Vec<Option<String>> = (0..N)
        .map(|i| {
            if i % 100 == 0 {
                None
            } else {
                Some(format!("{}{}", pool[i % pool.len()], i % 97))
            }
        })
        .collect();
    StringArray::from(
        owned
            .iter()
            .map(|o| o.as_deref())
            .collect::<Vec<Option<&str>>>(),
    )
}

fn main() {
    let mrows = N as f64 / 1e6;
    let tp = |m: f64| mrows / (m / 1000.0);
    println!("Arrow-columnar kernel pilot -- {N} rows, {RUNS}-run median wall\n");
    let arr = build_data();

    // lowercase (offset-preserving byte op): scalar vs generic vs specialized.
    let (s_lo, o_s) = bench(|| scalar_lowercase(&arr));
    let (g_lo, o_g) = bench(|| generic_lowercase(&arr));
    let (c_lo, o_c) = bench(|| columnar_lowercase(&arr));
    assert_same(&o_s, &o_g);
    assert_same(&o_s, &o_c);
    println!("lowercase (offset-preserving):");
    println!("  scalar-per-element   {s_lo:8.2} ms ({:6.1} M/s)  1.00x", tp(s_lo));
    println!("  generic buffer-write {g_lo:8.2} ms ({:6.1} M/s)  {:.2}x", tp(g_lo), s_lo / g_lo);
    println!("  specialized (SIMD)   {c_lo:8.2} ms ({:6.1} M/s)  {:.2}x", tp(c_lo), s_lo / c_lo);

    // strip (offset-changing, shrinks): scalar vs generic (no clean specialized).
    let (s_st, o_ss) = bench(|| scalar_strip(&arr));
    let (g_st, o_gs) = bench(|| generic_strip(&arr));
    assert_same(&o_ss, &o_gs);
    println!("\nstrip (offset-changing):");
    println!("  scalar-per-element   {s_st:8.2} ms ({:6.1} M/s)  1.00x", tp(s_st));
    println!("  generic buffer-write {g_st:8.2} ms ({:6.1} M/s)  {:.2}x", tp(g_st), s_st / g_st);

    // name_proper (compute-heavy per-string): scalar vs "columnar" (sizing only).
    let (s_sn, o_ssn) = bench(|| scalar_name_proper(&arr));
    let (c_sn, o_csn) = bench(|| columnar_name_proper(&arr));
    assert_same(&o_ssn, &o_csn);
    println!("\nname_proper (compute-heavy):");
    println!("  scalar-per-element   {s_sn:8.2} ms ({:6.1} M/s)  1.00x", tp(s_sn));
    println!("  columnar (sizing)    {c_sn:8.2} ms ({:6.1} M/s)  {:.2}x", tp(c_sn), s_sn / c_sn);

    println!(
        "\nDecision: if `generic buffer-write` captures most of the specialized win,\n\
         the sweep is ONE primitive (Fn(&str,&mut String)) adapting the trivial\n\
         family; if only `specialized` wins, it needs per-op buffer kernels."
    );
}
