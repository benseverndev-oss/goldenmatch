//! JNI entry points for `dev.goldensuite.spark.NativeScorer`.
//!
//! # What crosses the boundary, and why
//!
//! Not `String[]`. The obvious JNI signature takes two `jobjectArray`s of
//! `jstring` and calls `GetStringUTFChars` on each element -- which is a JNI
//! transition and usually a copy **per string**. At the batch size this arc
//! settled on (10,000 pairs) that is 20,000 JNI calls to avoid one, which
//! defeats the entire reason the plan was reshaped into batches in J1.
//!
//! So the Java side flattens to **Arrow's string layout** first (see
//! `Utf8Batch.java`) and passes primitive arrays: an `int[]` of `n + 1` offsets
//! and a `byte[]` of packed UTF-8, per side. This function then pins five
//! primitive arrays -- a fixed cost per *batch*, independent of how many pairs
//! it holds -- and hands their addresses straight to
//! [`goldenmatch_score_pairwise_utf8`], which is the exact layout that ABI was
//! designed around.
//!
//! # Pinning: non-critical, deliberately
//!
//! `GetPrimitiveArrayCritical` avoids a copy but suspends GC for the duration
//! and forbids any other JNI call inside the region. The copy it saves is one
//! `memcpy` per array per batch -- five memcpys of a few hundred KB against ten
//! thousand string comparisons. That is not where the time goes, and stalling
//! an executor's collector to save it is a bad trade. `get_array_elements`
//! (non-critical) it is.
//!
//! # Nulls
//!
//! Absent here, exactly as in `score-cabi`. Null slots arrive as zero-length
//! slices and the Java caller overwrites their scores with `null` afterwards,
//! because it holds the presence mask. Encoding "missing" as `""` down here is
//! the substitution that once made null-vs-null score a perfect 1.0, merging
//! records whose only shared evidence was a shared absence.
//!
//! # Errors are return codes, not exceptions
//!
//! Throwing from JNI means the caller must check `ExceptionOccurred` after
//! every call, and a missed check leaves a pending exception that detonates
//! somewhere unrelated. A negative `int` return cannot be ignored silently by
//! the Java side, because `NativeScorer` turns it into a message naming the
//! code. Codes `-1..=-4` are score-cabi's own, passed through unchanged; this
//! layer adds `-10..=-12` for faults it can detect and score-cabi cannot (it is
//! handed pointers, not arrays, so it cannot know an array was too short).

use goldenmatch_score_cabi::goldenmatch_score_pairwise_utf8;
use jni::objects::{JByteArray, JClass, JDoubleArray, JIntArray, JString, ReleaseMode};
use jni::sys::jint;
use jni::JNIEnv;

/// Error codes added by this layer. score-cabi's own codes (`-1..=-4`) pass
/// through unchanged, so a caller reads one table.
pub mod error {
    /// An array was shorter than `n` requires. score-cabi validates offset
    /// *contents* but is handed raw pointers, so only this layer can see that
    /// the `int[]` holding them has fewer than `n + 1` slots.
    pub const BAD_ARRAY_LENGTH: i32 = -10;
    /// `scorer_id` did not fit in the `u8` the kernel dispatches on.
    pub const SCORER_ID_OUT_OF_RANGE: i32 = -11;
    /// A JNI array could not be pinned.
    pub const JNI_ERROR: i32 = -12;
}

/// [`error`] code as a human-readable reason, for the Java side's message.
///
/// A free function on the Rust side rather than a table in Java so the codes
/// and their meanings live next to each other; drift between the two would
/// produce a confident, wrong diagnosis of a real failure.
pub fn describe(code: i32) -> &'static str {
    match code {
        0 => "ok",
        -1 => "a required buffer pointer was NULL",
        -2 => "batch length was negative or unrepresentable",
        -3 => "offsets were decreasing, negative, or ran past the data buffer",
        -4 => "a slice was not valid UTF-8",
        error::BAD_ARRAY_LENGTH => "an array was too short for the batch length",
        error::SCORER_ID_OUT_OF_RANGE => "scorer id outside 0..=255",
        error::JNI_ERROR => "a JNI array could not be pinned",
        _ => "unknown error code",
    }
}

/// `goldenmatch_score_abi_version()`, for the load-time skew gate.
///
/// The host refuses a library whose ABI it does not recognise rather than
/// calling it and reading the result wrongly -- this repo has already paid once
/// for a caller silently disagreeing with the kernel it loaded.
#[no_mangle]
pub extern "system" fn Java_dev_goldensuite_spark_NativeScorer_abiVersion(
    _env: JNIEnv,
    _class: JClass,
) -> jint {
    goldenmatch_score_cabi::goldenmatch_score_abi_version() as jint
}

/// `goldenmatch_score_scorer_id_count()`: how many ids `score_one` dispatches.
///
/// The host gates on this so an id the loaded kernel does not know is refused
/// instead of falling to the catch-all arm and scoring a confident 0.0.
#[no_mangle]
pub extern "system" fn Java_dev_goldensuite_spark_NativeScorer_scorerIdCount(
    _env: JNIEnv,
    _class: JClass,
) -> jint {
    goldenmatch_score_cabi::goldenmatch_score_scorer_id_count() as jint
}

/// Score `n` pairs from Arrow-layout primitive arrays into `out`.
///
/// Returns `0` on success or a negative code (see [`error`] and [`describe`]).
/// On any error `out` is left as the caller allocated it -- a caller that
/// ignores the code gets zeros, not a plausible-looking wrong score.
#[no_mangle]
pub extern "system" fn Java_dev_goldensuite_spark_NativeScorer_scorePairwiseUtf8<'local>(
    mut env: JNIEnv<'local>,
    _class: JClass<'local>,
    scorer_id: jint,
    a_offsets: JIntArray<'local>,
    a_data: JByteArray<'local>,
    b_offsets: JIntArray<'local>,
    b_data: JByteArray<'local>,
    n: jint,
    out: JDoubleArray<'local>,
) -> jint {
    if n < 0 {
        return -2; // score-cabi's BAD_LENGTH, reported before we pin anything.
    }
    let scorer_id = match u8::try_from(scorer_id) {
        Ok(v) => v,
        Err(_) => return error::SCORER_ID_OUT_OF_RANGE,
    };
    let n_usize = n as usize;

    // SAFETY: the arrays are live for the call (the JVM holds the references
    // that were passed in), and every `AutoElements` releases on drop. Held
    // simultaneously on purpose -- the kernel reads all four inputs and writes
    // `out` in one pass.
    unsafe {
        let ao = match env.get_array_elements(&a_offsets, ReleaseMode::NoCopyBack) {
            Ok(v) => v,
            Err(_) => return error::JNI_ERROR,
        };
        let ad = match env.get_array_elements(&a_data, ReleaseMode::NoCopyBack) {
            Ok(v) => v,
            Err(_) => return error::JNI_ERROR,
        };
        let bo = match env.get_array_elements(&b_offsets, ReleaseMode::NoCopyBack) {
            Ok(v) => v,
            Err(_) => return error::JNI_ERROR,
        };
        let bd = match env.get_array_elements(&b_data, ReleaseMode::NoCopyBack) {
            Ok(v) => v,
            Err(_) => return error::JNI_ERROR,
        };
        // CopyBack: this is the only array written, and without it the scores
        // would be computed into a JVM-side copy that is then discarded --
        // a silent all-zeros result rather than a failure.
        let mut o = match env.get_array_elements(&out, ReleaseMode::CopyBack) {
            Ok(v) => v,
            Err(_) => return error::JNI_ERROR,
        };

        // Extents, which score-cabi cannot check: it receives pointers.
        // Without this an `n` larger than the arrays reads past them, and the
        // whole reason that ABI validates offsets is to keep a JIT-compiled
        // host from doing exactly that.
        if ao.len() < n_usize + 1 || bo.len() < n_usize + 1 || o.len() < n_usize {
            return error::BAD_ARRAY_LENGTH;
        }

        goldenmatch_score_pairwise_utf8(
            scorer_id,
            ao.as_ptr(),
            // jbyte is i8; Arrow's data buffer is bytes either way. This is a
            // reinterpret of the same address, not a conversion.
            ad.as_ptr() as *const u8,
            ad.len() as i64,
            bo.as_ptr(),
            bd.as_ptr() as *const u8,
            bd.len() as i64,
            n as i64,
            o.as_mut_ptr(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The JNI entry points need a JVM, so what is testable here is the code
    /// table and the fact that this crate reports the SAME kernel identity as
    /// the C ABI it wraps. A skew between them would mean the load-time gate is
    /// checking one library and the scoring is done by another.
    #[test]
    fn abi_identity_matches_the_c_abi_it_wraps() {
        assert_eq!(goldenmatch_score_cabi::goldenmatch_score_abi_version(), 1);
        assert_eq!(
            goldenmatch_score_cabi::goldenmatch_score_scorer_id_count(),
            15
        );
    }

    #[test]
    fn every_code_this_layer_can_return_has_a_description() {
        for code in [
            0,
            -1,
            -2,
            -3,
            -4,
            error::BAD_ARRAY_LENGTH,
            error::SCORER_ID_OUT_OF_RANGE,
            error::JNI_ERROR,
        ] {
            assert_ne!(
                describe(code),
                "unknown error code",
                "code {code} has no description; a real failure would be \
                 diagnosed as 'unknown'"
            );
        }
        assert_eq!(describe(-99), "unknown error code");
    }

    /// The codes this layer adds must not collide with score-cabi's, because
    /// they are returned through the same `int` and read from one table.
    #[test]
    fn added_codes_do_not_collide_with_the_c_abi_codes() {
        let cabi = [
            goldenmatch_score_cabi::error::NULL_POINTER,
            goldenmatch_score_cabi::error::BAD_LENGTH,
            goldenmatch_score_cabi::error::BAD_OFFSETS,
            goldenmatch_score_cabi::error::INVALID_UTF8,
        ];
        for added in [
            error::BAD_ARRAY_LENGTH,
            error::SCORER_ID_OUT_OF_RANGE,
            error::JNI_ERROR,
        ] {
            assert!(
                !cabi.contains(&added),
                "code {added} is already a score-cabi code"
            );
        }
    }
}

// ── record fingerprints (identity graph) ────────────────────────────
//
// A second kernel in the same library. The jar carries one `.so` per platform,
// extracted and dlopen'd once per executor JVM, so a second library would buy a
// second extraction and a second load and nothing else.
//
// The boundary here is a JSON STRING, not Arrow buffers, and that is a
// deliberate difference from the scorer. Scoring is quadratic in block size --
// millions of calls -- so its per-call cost had to be amortised over a batch.
// Fingerprinting is once per RECORD: linear, and orders of magnitude fewer
// calls. `fingerprint_core::fingerprint_json` already accepts exactly this shape
// (its manifest names "non-Python callers (pgrx / DuckDB / C ABI)" as the
// reason), and Spark can build the JSON with `to_json(struct(*))` in the engine
// with no Python anywhere. Inventing an Arrow protocol here would add a
// marshaling layer to save a cost that is not being paid.

/// Canonical record fingerprint of a JSON object, as 64 lowercase hex chars.
///
/// Returns `null` on any failure -- invalid JSON, a non-object, an unsupported
/// value -- rather than throwing. A JNI exception must be checked for after
/// every call and a missed check detonates somewhere unrelated; a null is
/// visible in the result column and the Java side turns it into a message.
#[no_mangle]
pub extern "system" fn Java_dev_goldensuite_spark_NativeFingerprint_fingerprintJson<'local>(
    mut env: JNIEnv<'local>,
    _class: JClass<'local>,
    json: JString<'local>,
) -> jni::sys::jstring {
    let null = std::ptr::null_mut();
    let Ok(s) = env.get_string(&json) else {
        return null;
    };
    let Ok(text) = s.to_str() else {
        // Not valid UTF-8. JNI hands back modified UTF-8, so this is reachable
        // for lone surrogates -- refuse rather than hash something else.
        return null;
    };
    match goldenmatch_fingerprint_core::fingerprint_json(text) {
        Ok(hex) => match env.new_string(hex) {
            Ok(js) => js.into_raw(),
            Err(_) => null,
        },
        Err(_) => null,
    }
}

// ── transform chain (normalization) ─────────────────────────────────
//
// Third kernel in the same library. The chain arrives as ONE comma-separated
// string rather than a `String[]`: transform names contain `:` (`substring:0:3`)
// but never `,`, and a per-row `jobjectArray` would be a JNI transition per
// element to deliver a value that is the same on every row of the query.

fn split_chain(spec: &str) -> Vec<&str> {
    spec.split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect()
}

/// Whether every transform in a comma-separated chain can run here.
///
/// Exists so a host can refuse at PLAN time with the offending name, rather
/// than per row. `bloom_filter` and plugin transforms are Python-only by
/// design, and discovering that mid-job -- after the plan is distributed -- is
/// strictly worse than refusing before it starts.
#[no_mangle]
pub extern "system" fn Java_dev_goldensuite_spark_NativeTransform_supportsChain<'local>(
    mut env: JNIEnv<'local>,
    _class: JClass<'local>,
    chain: JString<'local>,
) -> jni::sys::jboolean {
    let Ok(s) = env.get_string(&chain) else {
        return 0;
    };
    let Ok(spec) = s.to_str() else {
        return 0;
    };
    let all = split_chain(spec)
        .iter()
        .all(|t| goldenmatch_transforms_core::supports(t));
    u8::from(all)
}

/// Apply a comma-separated transform chain to one value.
///
/// Returns `null` when the value is missing, when a transform legitimately
/// yields a missing value (`strip_honorifics` on an honorific-only name -- an
/// ABSENCE, not an empty string, because Fellegi-Sunter reads empty as an
/// agreement on nothing), or when the chain contains something this kernel
/// cannot run. The last case is unreachable in practice: the host gates on
/// `supportsChain` before the plan is built.
#[no_mangle]
pub extern "system" fn Java_dev_goldensuite_spark_NativeTransform_applyChain<'local>(
    mut env: JNIEnv<'local>,
    _class: JClass<'local>,
    value: JString<'local>,
    chain: JString<'local>,
) -> jni::sys::jstring {
    let null = std::ptr::null_mut();
    let (Ok(v), Ok(c)) = (env.get_string(&value), env.get_string(&chain)) else {
        return null;
    };
    let (Ok(text), Ok(spec)) = (v.to_str(), c.to_str()) else {
        return null;
    };
    let names = split_chain(spec);
    match goldenmatch_transforms_core::apply_transforms(Some(text), &names) {
        Ok(Some(out)) => match env.new_string(out) {
            Ok(js) => js.into_raw(),
            Err(_) => null,
        },
        Ok(None) | Err(_) => null,
    }
}

// ── survivorship (golden records) ───────────────────────────────────
//
// Fourth kernel in the same library. The values reuse `Utf8Batch`'s Arrow
// layout rather than arriving as a `String[]`: the marshaling is already
// written, already tested, and already handles the multi-byte case that a
// per-element `GetStringUTFChars` loop gets wrong. A presence mask rides
// alongside because a null MEMBER is not an empty value -- survivorship ignores
// absent members rather than voting for "".

/// Choose the surviving value for one cluster.
///
/// Returns `null` when there is no survivor (no non-null members, or
/// `unanimous_or_null` on a disagreement) and when the strategy is refused.
/// The host gates on [`Java_dev_goldensuite_spark_NativeSurvivorship_supportsStrategy`]
/// at plan time, so a refusal is unreachable in practice.
#[no_mangle]
pub extern "system" fn Java_dev_goldensuite_spark_NativeSurvivorship_mergeField<'local>(
    mut env: JNIEnv<'local>,
    _class: JClass<'local>,
    offsets: JIntArray<'local>,
    data: JByteArray<'local>,
    present: jni::objects::JBooleanArray<'local>,
    n: jint,
    strategy: JString<'local>,
) -> jni::sys::jstring {
    let null = std::ptr::null_mut();
    if n < 0 {
        return null;
    }
    let Ok(strat) = env.get_string(&strategy) else {
        return null;
    };
    let Ok(strat) = strat.to_str().map(str::to_owned) else {
        return null;
    };

    let n = n as usize;
    // SAFETY: the arrays are live for the call; every AutoElements releases on
    // drop. Values are copied out before scoring because `merge_field` borrows
    // them and the pins must not outlive this scope.
    let values: Vec<Option<String>> = unsafe {
        let (Ok(off), Ok(dat), Ok(pres)) = (
            env.get_array_elements(&offsets, ReleaseMode::NoCopyBack),
            env.get_array_elements(&data, ReleaseMode::NoCopyBack),
            env.get_array_elements(&present, ReleaseMode::NoCopyBack),
        ) else {
            return null;
        };
        if off.len() < n + 1 || pres.len() < n {
            return null;
        }
        let bytes: &[u8] = std::slice::from_raw_parts(dat.as_ptr() as *const u8, dat.len());
        let mut out = Vec::with_capacity(n);
        for i in 0..n {
            if pres[i] == 0 {
                out.push(None);
                continue;
            }
            let (s, e) = (off[i] as usize, off[i + 1] as usize);
            if s > e || e > bytes.len() {
                return null;
            }
            match std::str::from_utf8(&bytes[s..e]) {
                Ok(v) => out.push(Some(v.to_owned())),
                Err(_) => return null,
            }
        }
        out
    };

    let refs: Vec<Option<&str>> = values.iter().map(|v| v.as_deref()).collect();
    match goldenmatch_survivorship_core::merge_field(&refs, &strat) {
        Ok(Some(v)) => match env.new_string(v) {
            Ok(js) => js.into_raw(),
            Err(_) => null,
        },
        Ok(None) | Err(_) => null,
    }
}

/// Whether the loaded kernel can run `strategy`.
///
/// So a host refuses at PLAN time with the strategy named. `source_priority`
/// and `most_recent` need arguments the Spark path does not pass, and `custom:*`
/// is arbitrary Python -- discovering that per row, mid-job, would be strictly
/// worse than refusing before the plan is built.
#[no_mangle]
pub extern "system" fn Java_dev_goldensuite_spark_NativeSurvivorship_supportsStrategy<'local>(
    mut env: JNIEnv<'local>,
    _class: JClass<'local>,
    strategy: JString<'local>,
) -> jni::sys::jboolean {
    let Ok(s) = env.get_string(&strategy) else {
        return 0;
    };
    let Ok(name) = s.to_str() else {
        return 0;
    };
    u8::from(goldenmatch_survivorship_core::supports(name))
}
