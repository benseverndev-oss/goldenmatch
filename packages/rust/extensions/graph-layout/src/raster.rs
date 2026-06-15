//! Dependency-free anti-aliased software rasterizer → binary PPM (P6) frames.
//!
//! The honest framing: drawing nodes and edges to a buffer is the *cheap* part —
//! the layout solver is where the perf lives. So this stays small and depends on
//! nothing. PPM is trivial to emit and ffmpeg reads it directly:
//!
//! ```text
//! ffmpeg -framerate 30 -i frames/frame_%05d.ppm -pix_fmt yuv420p out.mp4
//! ```
//!
//! Edges use a simplified Xiaolin-Wu line; nodes use coverage-AA discs. Both
//! alpha-blend over the buffer, so dense edge regions build up into a glow.
//! (`--features skia` swaps in tiny-skia for higher-quality AA + PNG output.)

use std::fs::File;
use std::io::{self, BufWriter, Write};
use std::path::Path;

pub struct Canvas {
    pub w: usize,
    pub h: usize,
    buf: Vec<[u8; 3]>,
}

impl Canvas {
    pub fn new(w: usize, h: usize, bg: [u8; 3]) -> Self {
        Canvas {
            w,
            h,
            buf: vec![bg; w * h],
        }
    }

    #[inline]
    fn blend(&mut self, x: i32, y: i32, color: [u8; 3], a: f32) {
        if a <= 0.0 || x < 0 || y < 0 || x as usize >= self.w || y as usize >= self.h {
            return;
        }
        let a = a.min(1.0);
        let px = &mut self.buf[y as usize * self.w + x as usize];
        for c in 0..3 {
            px[c] = (color[c] as f32 * a + px[c] as f32 * (1.0 - a)).round() as u8;
        }
    }

    /// Anti-aliased line (simplified Wu: two-pixel coverage per major-axis step).
    pub fn line(&mut self, x0: f32, y0: f32, x1: f32, y1: f32, color: [u8; 3], alpha: f32) {
        let (mut x0, mut y0, mut x1, mut y1) = (x0, y0, x1, y1);
        let steep = (y1 - y0).abs() > (x1 - x0).abs();
        if steep {
            std::mem::swap(&mut x0, &mut y0);
            std::mem::swap(&mut x1, &mut y1);
        }
        if x0 > x1 {
            std::mem::swap(&mut x0, &mut x1);
            std::mem::swap(&mut y0, &mut y1);
        }
        let dx = x1 - x0;
        let grad = if dx.abs() < 1e-6 { 1.0 } else { (y1 - y0) / dx };

        let xstart = x0.round() as i32;
        let xend = x1.round() as i32;
        let mut intery = y0 + grad * (xstart as f32 - x0);
        for x in xstart..=xend {
            let y = intery.floor();
            let f = intery - y;
            let (yi, yi1) = (y as i32, y as i32 + 1);
            // steep: major axis is screen-y, so (px,py) = (minor, major).
            if steep {
                self.blend(yi, x, color, alpha * (1.0 - f));
                self.blend(yi1, x, color, alpha * f);
            } else {
                self.blend(x, yi, color, alpha * (1.0 - f));
                self.blend(x, yi1, color, alpha * f);
            }
            intery += grad;
        }
    }

    /// Coverage-anti-aliased filled disc.
    pub fn disc(&mut self, cx: f32, cy: f32, r: f32, color: [u8; 3], alpha: f32) {
        let x0 = (cx - r - 1.0).floor() as i32;
        let x1 = (cx + r + 1.0).ceil() as i32;
        let y0 = (cy - r - 1.0).floor() as i32;
        let y1 = (cy + r + 1.0).ceil() as i32;
        for py in y0..=y1 {
            for px in x0..=x1 {
                let dx = px as f32 + 0.5 - cx;
                let dy = py as f32 + 0.5 - cy;
                let dist = (dx * dx + dy * dy).sqrt();
                let cov = (r + 0.5 - dist).clamp(0.0, 1.0); // 1px soft edge
                if cov > 0.0 {
                    self.blend(px, py, color, alpha * cov);
                }
            }
        }
    }

    /// Write binary PPM (P6).
    pub fn save_ppm(&self, path: &Path) -> io::Result<()> {
        let f = File::create(path)?;
        let mut w = BufWriter::new(f);
        write!(w, "P6\n{} {}\n255\n", self.w, self.h)?;
        // Vec<[u8;3]> is contiguous RGB; write it in one shot.
        let bytes = unsafe {
            std::slice::from_raw_parts(self.buf.as_ptr() as *const u8, self.buf.len() * 3)
        };
        w.write_all(bytes)?;
        w.flush()
    }
}

/// A small, visually distinct palette indexed by component id (clusters read as
/// different colors). Wraps around past its length.
pub const PALETTE: &[[u8; 3]] = &[
    [99, 155, 255],  // blue
    [255, 138, 101], // coral
    [129, 199, 132], // green
    [186, 144, 255], // violet
    [255, 213, 79],  // amber
    [77, 208, 225],  // cyan
    [240, 98, 146],  // pink
    [174, 213, 129], // lime
    [255, 183, 77],  // orange
    [149, 117, 205], // purple
];

#[inline]
pub fn color_for(component: u32) -> [u8; 3] {
    PALETTE[component as usize % PALETTE.len()]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blends_and_clips() {
        let mut c = Canvas::new(4, 4, [0, 0, 0]);
        c.disc(1.5, 1.5, 1.0, [200, 100, 50], 1.0);
        c.line(0.0, 0.0, 3.0, 3.0, [255, 255, 255], 0.5);
        // Drawing fully out of bounds must not panic.
        c.disc(-50.0, -50.0, 3.0, [255, 255, 255], 1.0);
        c.line(100.0, 100.0, 200.0, 200.0, [255, 255, 255], 1.0);
        assert_eq!(c.buf.len(), 16);
    }
}
