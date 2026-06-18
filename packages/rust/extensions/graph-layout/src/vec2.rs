//! Minimal 2D vector math (f32). No external dep; kept tiny so the hot loops
//! inline cleanly.

use std::ops::{Add, Sub};

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct V2 {
    pub x: f32,
    pub y: f32,
}

impl V2 {
    pub const ZERO: V2 = V2 { x: 0.0, y: 0.0 };

    #[inline]
    pub fn new(x: f32, y: f32) -> Self {
        V2 { x, y }
    }
    #[inline]
    pub fn scale(self, s: f32) -> V2 {
        V2::new(self.x * s, self.y * s)
    }
    #[inline]
    pub fn len2(self) -> f32 {
        self.x * self.x + self.y * self.y
    }
    #[inline]
    pub fn len(self) -> f32 {
        self.len2().sqrt()
    }
}

impl Add for V2 {
    type Output = V2;
    #[inline]
    fn add(self, o: V2) -> V2 {
        V2::new(self.x + o.x, self.y + o.y)
    }
}

impl Sub for V2 {
    type Output = V2;
    #[inline]
    fn sub(self, o: V2) -> V2 {
        V2::new(self.x - o.x, self.y - o.y)
    }
}
