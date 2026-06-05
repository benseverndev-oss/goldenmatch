//! Deterministic first-seen string→i64 dictionary, identical across the DuckDB,
//! Postgres, and DataFusion wrappers so string-id grouping + round-trip agree.

use std::collections::HashMap;

#[derive(Default)]
pub struct Dict {
    to_id: HashMap<String, i64>,
    to_str: Vec<String>,
}

impl Dict {
    pub fn new() -> Self { Self::default() }
    /// Return the i64 for `s`, assigning the next id on first sight.
    pub fn intern(&mut self, s: &str) -> i64 {
        if let Some(&id) = self.to_id.get(s) { return id; }
        let id = self.to_str.len() as i64;
        self.to_id.insert(s.to_string(), id);
        self.to_str.push(s.to_string());
        id
    }
    pub fn resolve(&self, id: i64) -> Option<&str> {
        self.to_str.get(id as usize).map(|s| s.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_seen_order_is_deterministic() {
        let mut d = Dict::new();
        assert_eq!(d.intern("b"), 0);
        assert_eq!(d.intern("a"), 1);
        assert_eq!(d.intern("b"), 0); // stable
        assert_eq!(d.resolve(1), Some("a"));
    }
}
