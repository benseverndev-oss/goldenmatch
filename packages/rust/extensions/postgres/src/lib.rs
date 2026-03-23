use pgrx::prelude::*;

pgrx::pg_module_magic!();

mod quick;

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
    pub fn setup(_options: Vec<&str>) {}

    pub fn postgresql_conf_options() -> Vec<&'static str> {
        vec![]
    }
}
