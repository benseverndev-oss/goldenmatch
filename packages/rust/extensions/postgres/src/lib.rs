//! GoldenMatch PostgreSQL Extension
//!
//! Provides SQL functions for entity resolution directly within PostgreSQL.
//!
//! Quick-start functions (public schema):
//!   - goldenmatch_dedupe(table_name, config_json) -> TABLE
//!   - goldenmatch_match(target_table, ref_table, config_json) -> TABLE
//!   - goldenmatch_score(value_a, value_b, scorer) -> float8
//!   - goldenmatch_score_pair(record_a, record_b, config_json) -> float8
//!   - goldenmatch_explain(record_a, record_b, config_json) -> text

use pgrx::prelude::*;

pgrx::pg_module_magic!();

mod quick;

// Re-export SQL functions
pub use quick::*;

/// Extension initialization -- verify Python + goldenmatch are available.
#[pg_guard]
pub extern "C" fn _PG_init() {
    match goldenmatch_bridge::init() {
        Ok(()) => {
            pgrx::log!("goldenmatch: Python bridge initialized successfully");
        }
        Err(e) => {
            pgrx::warning!("goldenmatch: {}", e);
        }
    }
}

#[cfg(any(test, feature = "pg_test"))]
#[pg_schema]
mod tests {
    use pgrx::prelude::*;

    #[pg_test]
    fn test_score_basic() {
        let score = crate::quick::goldenmatch_score(
            "John Smith".to_string(),
            "Jon Smyth".to_string(),
            Some("jaro_winkler".to_string()),
        );
        assert!(score > 0.7);
        assert!(score < 1.0);
    }

    #[pg_test]
    fn test_score_exact() {
        let score = crate::quick::goldenmatch_score(
            "hello".to_string(),
            "hello".to_string(),
            Some("exact".to_string()),
        );
        assert_eq!(score, 1.0);
    }
}

#[cfg(test)]
pub mod pg_test {
    pub fn setup(_options: Vec<&str>) {
        // noop
    }

    pub fn postgresql_conf_options() -> Vec<&'static str> {
        vec![]
    }
}
