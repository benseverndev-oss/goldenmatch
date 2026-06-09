//! CLK (Cryptographic Longterm Key) bloom-filter hashing — the per-ngram /
//! per-hash double loop that dominates `pprl.compute_bloom_filters`.
//!
//! Parity contract — MUST match `goldenmatch.utils.transforms._clk_from_prepared`
//! byte-for-byte. The Python caller does ALL preprocessing (lower/strip/pad/
//! balanced-salt) and hands us the final `prepared` string, so the only Python
//! behaviour reproduced here is:
//!   * char-by-char n-gram slicing (Unicode scalar, NOT bytes),
//!   * digest = sha256(f"{k}:{ngram}")  OR  hmac_sha256(key=f"{hmac_key}:{k}", msg=ngram),
//!   * bit_pos = int(digest_hex, 16) % filter_size   (256-bit big-endian mod),
//!   * little-endian bit set within each byte: bits[pos/8] |= 1 << (pos%8),
//!   * lowercase hex of the bit buffer.
//!
//! Keeping all the Unicode-sensitive preprocessing in Python is deliberate: it
//! removes the `str.lower()`/`str.strip()` parity hazard (Python casing/whitespace
//! rules != Rust `to_lowercase()`/`trim()`), so the kernel is byte-exact by
//! construction. See `docs/design/2026-05-25-native-acceleration-decision-matrix.md`.
use hmac::{Hmac, Mac};
use pyo3::prelude::*;
use rayon::prelude::*;
use sha2::{Digest, Sha256};

type HmacSha256 = Hmac<Sha256>;

/// `int(hexdigest, 16) % m` without bignum: fold the 32 digest bytes MSB-first.
/// `digest[0]` is the most-significant byte (hexdigest is the bytes in order),
/// so this reproduces Python's `int(h, 16) % filter_size` exactly. `acc` stays
/// `< m` (filter_size <= a few thousand) so `acc * 256 + b` never overflows u64.
#[inline]
fn mod_be(digest: &[u8], m: u64) -> usize {
    let mut acc: u64 = 0;
    for &b in digest {
        acc = (acc * 256 + b as u64) % m;
    }
    acc as usize
}

#[inline]
fn to_hex(bytes: &[u8]) -> String {
    const LUT: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(LUT[(b >> 4) as usize] as char);
        s.push(LUT[(b & 0x0f) as usize] as char);
    }
    s
}

fn clk_one(
    prepared: &str,
    ngram_size: usize,
    num_hashes: usize,
    filter_size: usize,
    hmac_key: Option<&str>,
) -> String {
    let mut bits = vec![0u8; filter_size / 8];
    let chars: Vec<char> = prepared.chars().collect();
    let m = filter_size as u64;
    if chars.len() >= ngram_size {
        for i in 0..=(chars.len() - ngram_size) {
            let ngram: String = chars[i..i + ngram_size].iter().collect();
            for k in 0..num_hashes {
                let pos = match hmac_key {
                    Some(key) => {
                        // f"{hmac_key}:{k}" is the HMAC key; ngram is the message.
                        let mut mac = HmacSha256::new_from_slice(format!("{key}:{k}").as_bytes())
                            .expect("HMAC accepts any key length");
                        mac.update(ngram.as_bytes());
                        mod_be(&mac.finalize().into_bytes(), m)
                    }
                    None => {
                        let digest = Sha256::digest(format!("{k}:{ngram}").as_bytes());
                        mod_be(&digest, m)
                    }
                };
                bits[pos / 8] |= 1u8 << (pos % 8);
            }
        }
    }
    to_hex(&bits)
}

/// Batch CLK over a prepared column. `prepared` holds rows the Python caller has
/// already lowercased / stripped / padded / salted (None rows are filtered out
/// caller-side and stitched back). Returns one lowercase-hex CLK per input.
#[pyfunction]
#[pyo3(signature = (prepared, ngram_size, num_hashes, filter_size, hmac_key=None))]
pub fn bloom_clk_batch(
    py: Python<'_>,
    prepared: Vec<String>,
    ngram_size: usize,
    num_hashes: usize,
    filter_size: usize,
    hmac_key: Option<String>,
) -> PyResult<Vec<String>> {
    if filter_size == 0 || !filter_size.is_multiple_of(8) {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "filter_size must be a positive multiple of 8",
        ));
    }
    if ngram_size == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "ngram_size must be positive",
        ));
    }
    // No Python objects touched in the loop -> release the GIL + rayon fan-out,
    // exactly like score_block_pairs_arrow.
    let key = hmac_key.as_deref();
    Ok(py.allow_threads(|| {
        prepared
            .par_iter()
            .map(|p| clk_one(p, ngram_size, num_hashes, filter_size, key))
            .collect()
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mod_be_matches_bignum_intuition() {
        // 0x...01 mod 512 == 1; high bytes contribute (256^k mod m).
        let mut d = [0u8; 32];
        d[31] = 1;
        assert_eq!(mod_be(&d, 512), 1);
        d[31] = 0;
        d[0] = 1; // 256^31 mod 512 == 0 (256^k divisible by 512 for k>=2)
        assert_eq!(mod_be(&d, 512), 0);
    }

    #[test]
    fn clk_is_deterministic_and_sized() {
        let a = clk_one("john smith", 2, 20, 512, None);
        let b = clk_one("john smith", 2, 20, 512, None);
        assert_eq!(a, b);
        assert_eq!(a.len(), 512 / 8 * 2); // hex chars
    }

    #[test]
    fn hmac_differs_from_plain() {
        let plain = clk_one("john smith", 2, 20, 512, None);
        let keyed = clk_one("john smith", 2, 20, 512, Some("k:0_unused_prefix"));
        assert_ne!(plain, keyed);
    }
}
