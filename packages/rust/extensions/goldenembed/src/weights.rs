//! Read the learned projection weights from a numpy `weights.npz` — the file
//! `GoldenEmbedModel.save` always writes (`np.savez(path, weights=..., bias=...)`).
//! An `.npz` is a zip of `.npy` entries; the `zip` crate (already a dep) opens
//! it and each `.npy` is a tiny self-describing header + raw little-endian f32.
//! This is what lets the runtime run the projection **natively** (a matmul over
//! `goldenembed-core::project`) instead of through ONNX Runtime.

use anyhow::{bail, Context, Result};
use std::io::{Read, Seek};
use std::path::Path;

/// Load the row-major `(n_features, dim)` projection matrix + an optional
/// length-`dim` bias from `weights.npz`. Validates the flattened lengths against
/// `dim`.
pub fn load_npz(path: &Path, dim: usize) -> Result<(Vec<f32>, Option<Vec<f32>>)> {
    if dim == 0 {
        bail!("dim must be positive");
    }
    let file = std::fs::File::open(path).with_context(|| format!("open {}", path.display()))?;
    let mut zip =
        zip::ZipArchive::new(file).with_context(|| format!("read npz {}", path.display()))?;

    let weights = read_npy_f32(&mut zip, "weights.npy")?;
    if weights.is_empty() || !weights.len().is_multiple_of(dim) {
        bail!(
            "weights length {} is not a positive multiple of dim {}",
            weights.len(),
            dim
        );
    }
    let bias = match read_npy_f32_opt(&mut zip, "bias.npy")? {
        Some(b) if b.len() == dim => Some(b),
        Some(b) => bail!("bias length {} != dim {}", b.len(), dim),
        None => None,
    };
    Ok((weights, bias))
}

fn read_npy_f32_opt<R: Read + Seek>(
    zip: &mut zip::ZipArchive<R>,
    name: &str,
) -> Result<Option<Vec<f32>>> {
    // by_name errors when the entry is absent (bias is optional).
    if zip.index_for_name(name).is_none() {
        return Ok(None);
    }
    Ok(Some(read_npy_f32(zip, name)?))
}

fn read_npy_f32<R: Read + Seek>(zip: &mut zip::ZipArchive<R>, name: &str) -> Result<Vec<f32>> {
    let mut entry = zip
        .by_name(name)
        .with_context(|| format!("npz missing {name}"))?;
    let mut buf = Vec::new();
    entry.read_to_end(&mut buf)?;
    parse_npy_f32(&buf, name)
}

/// Parse a `.npy` array of little-endian f32 (C order) into a flat `Vec<f32>`.
/// Supports format versions 1.0 (u16 header len) and 2.0 (u32).
fn parse_npy_f32(buf: &[u8], name: &str) -> Result<Vec<f32>> {
    const MAGIC: &[u8] = b"\x93NUMPY";
    if buf.len() < 10 || &buf[..6] != MAGIC {
        bail!("{name}: not a .npy file");
    }
    let major = buf[6];
    let (header_start, header_len) = if major >= 2 {
        let len = u32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]) as usize;
        (12, len)
    } else {
        let len = u16::from_le_bytes([buf[8], buf[9]]) as usize;
        (10, len)
    };
    let data_start = header_start + header_len;
    if buf.len() < data_start {
        bail!("{name}: truncated header");
    }
    let header = std::str::from_utf8(&buf[header_start..data_start])
        .with_context(|| format!("{name}: non-utf8 header"))?;
    // Contract: little-endian f32, C order. The Python side always writes
    // `np.ascontiguousarray(..., dtype=np.float32)`, so this is exact — reject
    // anything else rather than silently misreading.
    if !(header.contains("'<f4'") || header.contains("\"<f4\"")) {
        bail!("{name}: dtype is not little-endian float32 (<f4); header: {header}");
    }
    if header.contains("'fortran_order': True") || header.contains("'fortran_order':True") {
        bail!("{name}: fortran_order arrays are not supported");
    }
    let data = &buf[data_start..];
    if !data.len().is_multiple_of(4) {
        bail!("{name}: data length {} is not a multiple of 4", data.len());
    }
    Ok(data
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect())
}
