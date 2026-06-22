//! S1 pair-count extrapolation kernel (spec
//! 2026-06-22-autoconfig-smarter-faster-s1-s3): corrects the sample->full
//! projection. Pairs scale by ratio^2 (within-block pairs are quadratic in
//! block size, so linear scaling under-counts), capped at the all-pairs
//! maximum; n_blocks uses a Chao1 richness estimate when F1/F2 were measured,
//! else a linear fallback. Integer-exact (u128 intermediates) so it is
//! byte-parity with the Python oracle `complexity_profile.extrapolate_to`.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtrapolationInput {
    pub total_comparisons: u64,
    pub n_blocks: u64,
    pub singleton_block_count: u64,
    /// Chao1 singleton-block count. `None` => not measured => linear n_blocks
    /// fallback (the exact build_blocks path leaves it None).
    pub chao1_f1: Option<u64>,
    pub chao1_f2: Option<u64>,
    pub n_rows_sample: u64,
    pub n_rows_full: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExtrapolationOutput {
    pub n_blocks: u64,
    pub total_comparisons: u64,
    pub singleton_block_count: u64,
}

pub fn extrapolate_pair_count(input: &ExtrapolationInput) -> ExtrapolationOutput {
    let ns = input.n_rows_sample;
    let nf = input.n_rows_full;
    if ns == 0 || nf == 0 {
        return ExtrapolationOutput {
            n_blocks: input.n_blocks,
            total_comparisons: input.total_comparisons,
            singleton_block_count: input.singleton_block_count,
        };
    }

    // Pairs: integer-exact ratio^2, capped at the all-pairs maximum. u128
    // intermediate (total_comparisons * nf^2 overflows u64).
    let pairs_raw =
        (input.total_comparisons as u128) * (nf as u128) * (nf as u128) / ((ns as u128) * (ns as u128));
    let cap = (nf as u128) * ((nf - 1) as u128) / 2;
    let pairs = pairs_raw.min(cap) as u64;

    // n_blocks: Chao1 richness when F1/F2 measured, else integer-floor linear.
    let blocks = match (input.chao1_f1, input.chao1_f2) {
        (Some(f1), Some(f2)) => {
            // u64 is intentional here (NOT u128): f1 is bounded by the sample
            // row count, so f1*f1 cannot overflow u64 at any sample size the
            // fallback produces; matches Python's arbitrary-precision result.
            // observed distinct blocks = measured size>=2 blocks + singletons.
            let observed = input.n_blocks + f1;
            (observed + f1 * f1 / (2 * (f2 + 1))).min(nf)
        }
        _ => {
            // linear fallback, integer-floor via u128 (avoids u64 overflow on n_blocks*nf)
            let linear = ((input.n_blocks as u128) * (nf as u128) / (ns as u128)) as u64;
            linear.min(nf)
        }
    };

    let singletons =
        ((input.singleton_block_count as u128) * (nf as u128) / (ns as u128)) as u64;

    ExtrapolationOutput {
        n_blocks: blocks,
        total_comparisons: pairs,
        singleton_block_count: singletons,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(
        tc: u64,
        nb: u64,
        f1: Option<u64>,
        f2: Option<u64>,
        ns: u64,
        nf: u64,
    ) -> ExtrapolationInput {
        ExtrapolationInput {
            total_comparisons: tc,
            n_blocks: nb,
            singleton_block_count: 0,
            chao1_f1: f1,
            chao1_f2: f2,
            n_rows_sample: ns,
            n_rows_full: nf,
        }
    }

    #[test]
    fn pairs_quadratic() {
        let o = extrapolate_pair_count(&input(100, 10, None, None, 1_000, 100_000));
        assert_eq!(o.total_comparisons, 1_000_000);
        assert_eq!(o.n_blocks, 1_000); // linear fallback
    }

    #[test]
    fn pairs_cap_inert_for_legit_input() {
        // legit tc (<= C(10,2)=45): raw=10*400/100=40 < cap 190 -> 40
        let o = extrapolate_pair_count(&input(10, 2, None, None, 10, 20));
        assert_eq!(o.total_comparisons, 40);
    }

    #[test]
    fn pairs_cap_clamps_pathological() {
        // pathological tc (> C(10,2)): raw=50*400/100=200 > cap 190 -> 190
        let o = extrapolate_pair_count(&input(50, 2, None, None, 10, 20));
        assert_eq!(o.total_comparisons, 190);
    }

    #[test]
    fn nblocks_chao1() {
        let o = extrapolate_pair_count(&input(100, 50, Some(10), Some(5), 1_000, 100_000));
        assert_eq!(o.n_blocks, 68); // (50+10) + 100/(2*6)=8
    }

    #[test]
    fn nblocks_chao1_capped_at_full_rows() {
        // many singletons, few doubletons -> Chao1 exceeds n_full -> cap at n_full
        let o = extrapolate_pair_count(&input(10, 10, Some(1_000), Some(0), 2_000, 50));
        assert_eq!(o.n_blocks, 50);
    }

    #[test]
    fn noop_bad_args() {
        let o = extrapolate_pair_count(&input(10, 5, None, None, 0, 100));
        assert_eq!(o.total_comparisons, 10);
        assert_eq!(o.n_blocks, 5);
    }
}
