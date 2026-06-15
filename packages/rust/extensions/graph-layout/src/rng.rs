//! Tiny deterministic PRNG (xorshift64). Dependency-free; only used for layout
//! init + synthetic graphs, where reproducibility matters more than statistical
//! quality.

pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Rng(seed | 1) // avoid the zero fixed point
    }

    #[inline]
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }

    /// Uniform f32 in [0, 1).
    #[inline]
    pub fn unit01(&mut self) -> f32 {
        (self.next_u64() >> 40) as f32 / (1u64 << 24) as f32
    }

    /// Uniform f32 in [-1, 1).
    #[inline]
    pub fn unit(&mut self) -> f32 {
        self.unit01() * 2.0 - 1.0
    }
}
