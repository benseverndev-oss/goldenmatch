//! The single, dependency-free 64-bit hash family that anchors cross-language
//! parity. Mirrors `goldenmatch/core/sketch.py` (`base_hash`, `splitmix64`) and
//! the TypeScript port byte-for-byte. All arithmetic is wrapping `u64`.

const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;
const SM_C1: u64 = 0xbf58_476d_1ce4_e5b9;
const SM_C2: u64 = 0x94d0_49bb_1331_11eb;
const SM_GAMMA: u64 = 0x9e37_79b9_7f4a_7c15;

/// FNV-1a (64-bit) over `data`, then a splitmix64 finalizer for avalanche.
#[inline]
pub fn base_hash(data: &[u8]) -> u64 {
    let mut h = FNV_OFFSET;
    for &byte in data {
        h = (h ^ byte as u64).wrapping_mul(FNV_PRIME);
    }
    h = (h ^ (h >> 30)).wrapping_mul(SM_C1);
    h = (h ^ (h >> 27)).wrapping_mul(SM_C2);
    h ^ (h >> 31)
}

/// One splitmix64 step. Returns `(value, new_state)`.
///
/// The increment is applied *before* finalization, so a stream seeded at `S`
/// produces its first value as `finalize(S + GAMMA)` — there is no raw-seed draw.
#[inline]
pub fn splitmix64(state: u64) -> (u64, u64) {
    let state = state.wrapping_add(SM_GAMMA);
    let mut z = state;
    z = (z ^ (z >> 30)).wrapping_mul(SM_C1);
    z = (z ^ (z >> 27)).wrapping_mul(SM_C2);
    z ^= z >> 31;
    (z, state)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base_hash_golden() {
        assert_eq!(base_hash(b""), 17665956581633026203);
        assert_eq!(base_hash(b"a"), 198367012849983736);
        assert_eq!(base_hash(b"ab"), 11528740771484442951);
        assert_eq!(base_hash(b"hello world"), 417524495691944273);
    }

    #[test]
    fn splitmix64_stream_from_zero() {
        let mut state = 0u64;
        let mut out = Vec::new();
        for _ in 0..4 {
            let (v, s) = splitmix64(state);
            out.push(v);
            state = s;
        }
        assert_eq!(
            out,
            vec![
                16294208416658607535,
                7960286522194355700,
                487617019471545679,
                17909611376780542444,
            ]
        );
    }
}
