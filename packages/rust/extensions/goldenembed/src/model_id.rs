//! Reproduce the Python `GoldenEmbedModel.model_id` from a saved model dir,
//! by hashing the raw array bytes inside `weights.npz` exactly as numpy's
//! `ndarray.tobytes()` would — so the Rust runtime computes the same cache
//! namespace as Python without a Python dependency.
use std::io::Read;
use std::path::Path;

use anyhow::{anyhow, Context, Result};
use blake2::digest::{Update, VariableOutput};
use blake2::Blake2bVar;

/// Lowercase hex of a byte slice (matches Python `hexdigest()`).
pub(crate) fn hex(bytes: &[u8]) -> String {
    use std::fmt::Write as FmtWrite;
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        let _ = write!(s, "{b:02x}");
    }
    s
}

/// Extract the raw data section of a `.npy` blob (skip magic + header).
fn npy_data(buf: &[u8]) -> Result<&[u8]> {
    if buf.len() < 10 || &buf[0..6] != b"\x93NUMPY" {
        return Err(anyhow!("not a .npy buffer"));
    }
    let major = buf[6];
    let data_start = if major >= 2 {
        if buf.len() < 12 {
            return Err(anyhow!("truncated v2 .npy header"));
        }
        12 + u32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]) as usize
    } else {
        10 + u16::from_le_bytes([buf[8], buf[9]]) as usize
    };
    buf.get(data_start..)
        .ok_or_else(|| anyhow!("truncated .npy"))
}

fn read_zip_entry(zip_path: &Path, name: &str) -> Result<Option<Vec<u8>>> {
    let file =
        std::fs::File::open(zip_path).with_context(|| format!("opening {}", zip_path.display()))?;
    let mut archive = zip::ZipArchive::new(file)?;
    let result = match archive.by_name(name) {
        Ok(mut entry) => {
            let mut buf = Vec::new();
            entry.read_to_end(&mut buf)?;
            Ok(Some(buf))
        }
        Err(zip::result::ZipError::FileNotFound) => Ok(None),
        Err(e) => Err(anyhow::Error::from(e)),
    };
    result
}

/// Reproduce `inhouse:d{dim}:{blake2b8(weights[+bias])}` from `<dir>/weights.npz`.
pub fn compute_model_id(dir: &Path, dim: usize) -> Result<String> {
    let zip_path = dir.join("weights.npz");
    let weights = read_zip_entry(&zip_path, "weights.npy")?
        .ok_or_else(|| anyhow!("weights.npy missing from {}", zip_path.display()))?;
    let mut hasher = Blake2bVar::new(8).expect("blake2b-8 is valid");
    hasher.update(npy_data(&weights)?);
    if let Some(bias) = read_zip_entry(&zip_path, "bias.npy")? {
        hasher.update(npy_data(&bias)?);
    }
    let mut out = [0u8; 8];
    hasher
        .finalize_variable(&mut out)
        .expect("8-byte output fits");
    Ok(format!("inhouse:d{dim}:{}", hex(&out)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn fixture(name: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures")
            .join(name)
    }

    #[test]
    fn model_id_matches_python_no_bias() {
        let got = compute_model_id(&fixture("tiny_model"), 8).unwrap();
        assert_eq!(got, EXPECTED_NO_BIAS);
    }

    #[test]
    fn model_id_matches_python_with_bias() {
        let got = compute_model_id(&fixture("tiny_model_bias"), 8).unwrap();
        assert_eq!(got, EXPECTED_BIAS);
    }

    const EXPECTED_NO_BIAS: &str = "PLACEHOLDER_NO_BIAS";
    const EXPECTED_BIAS: &str = "PLACEHOLDER_BIAS";
}
