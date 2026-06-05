//! redb-backed two-tier embedding cache keyed by Python-parity
//! `(model_id, text_hash)`. Rust-only on-disk format by design (edge runtime);
//! cross-language sharing with the Python SQLite cache is a non-goal.
use std::collections::HashMap;
use std::path::Path;

use anyhow::Result;
use redb::{Database, TableDefinition};
use sha2::{Digest, Sha256};

const TABLE: TableDefinition<&str, &[u8]> = TableDefinition::new("embeddings");

/// Port of Python `goldenmatch.embeddings.normalize_text`:
/// collapse all whitespace runs to single spaces, then lowercase.
/// `split_whitespace` uses Unicode `White_Space`; Python `str.split` uses
/// `str.isspace` — they agree on ASCII + common Unicode whitespace. Known
/// divergence (documented, non-critical: a mismatch only forces a cache miss):
/// Python treats C0 separators `\x1c`–`\x1f` and `\x85` as whitespace; Rust
/// does not.
pub fn normalize_text(text: &str) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

/// Port of Python `text_hash`: lowercase hex SHA-256 of the UTF-8 bytes.
pub fn text_hash(normalized: &str) -> String {
    let digest = Sha256::digest(normalized.as_bytes());
    crate::model_id::hex(&digest)
}

/// Two-tier cache: in-memory HashMap front + optional redb disk tier.
pub struct EmbedCache {
    mem: HashMap<String, Vec<f32>>,
    db: Option<Database>,
}

fn key(model_id: &str, text_hash: &str) -> String {
    // `\u{0}` cannot appear in a hex hash or the model_id format, so this is a
    // collision-free composite key.
    format!("{model_id}\u{0}{text_hash}")
}

fn to_bytes(vec: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vec.len() * 4);
    for v in vec {
        out.extend_from_slice(&v.to_le_bytes());
    }
    out
}

fn from_bytes(bytes: &[u8]) -> Vec<f32> {
    bytes
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect()
}

impl EmbedCache {
    /// Ephemeral mem-only cache.
    pub fn in_memory() -> Self {
        Self {
            mem: HashMap::new(),
            db: None,
        }
    }

    /// Open/create a redb-backed cache at `path`.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let db = Database::create(path.as_ref())?;
        // Ensure the table exists so first-read on a fresh db doesn't error.
        let w = db.begin_write()?;
        {
            let _ = w.open_table(TABLE)?;
        }
        w.commit()?;
        Ok(Self {
            mem: HashMap::new(),
            db: Some(db),
        })
    }

    pub fn get(&mut self, model_id: &str, text_hash: &str) -> Option<Vec<f32>> {
        let k = key(model_id, text_hash);
        if let Some(hit) = self.mem.get(&k) {
            return Some(hit.clone());
        }
        let db = self.db.as_ref()?;
        let r = db.begin_read().ok()?;
        let t = r.open_table(TABLE).ok()?;
        let v = t.get(k.as_str()).ok().flatten()?;
        let vec = from_bytes(v.value());
        self.mem.insert(k, vec.clone()); // promote to mem tier
        Some(vec)
    }

    pub fn put(&mut self, model_id: &str, text_hash: &str, vec: Vec<f32>) -> Result<()> {
        let k = key(model_id, text_hash);
        if let Some(db) = self.db.as_ref() {
            let w = db.begin_write()?;
            {
                let mut t = w.open_table(TABLE)?;
                t.insert(k.as_str(), to_bytes(&vec).as_slice())?;
            }
            w.commit()?;
        }
        self.mem.insert(k, vec);
        Ok(())
    }

    /// Number of entries in the in-memory tier.
    pub fn len(&self) -> usize {
        self.mem.len()
    }

    /// True when the in-memory tier holds no entries.
    pub fn is_empty(&self) -> bool {
        self.mem.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_collapses_and_lowercases() {
        assert_eq!(normalize_text("Acme  Corp"), "acme corp");
        assert_eq!(normalize_text("  John\tSmith\n"), "john smith");
        assert_eq!(normalize_text(""), "");
    }

    #[test]
    fn text_hash_matches_python_sha256() {
        // Digests computed independently via `printf '<t>' | sha256sum`.
        assert_eq!(
            text_hash("acme corp"),
            "ea6f9c07a2f95c788a1645cf557f58aa63c5fa3ad7d749b9db4fce435deef64e"
        );
        assert_eq!(
            text_hash("john smith"),
            "32ddaf65cc3aa8d3e6eda3ca2da7c18b71e169e9aa444cccb479c9ca759dd095"
        );
        assert_eq!(
            text_hash(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn mem_roundtrip() {
        let mut c = EmbedCache::in_memory();
        assert!(c.get("m", "h").is_none());
        c.put("m", "h", vec![1.0, 2.0, 3.0]).unwrap();
        assert_eq!(c.get("m", "h"), Some(vec![1.0, 2.0, 3.0]));
    }

    #[test]
    fn redb_persists_across_reopen() {
        let dir = std::env::temp_dir().join(format!("gec_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("cache.redb");
        {
            let mut c = EmbedCache::open(&path).unwrap();
            c.put("m", "h", vec![0.5, -0.5]).unwrap();
        }
        let mut c2 = EmbedCache::open(&path).unwrap();
        assert_eq!(c2.get("m", "h"), Some(vec![0.5, -0.5]));
        std::fs::remove_dir_all(&dir).ok();
    }
}
