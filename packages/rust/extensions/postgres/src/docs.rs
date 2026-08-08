//! `goldenmatch_docs()` — the in-SQL orientation function.
//!
//! An agent that meets GoldenMatch through a database connection has no
//! filesystem to read and no package to import: it sees eighty function names
//! and nothing else, so it reverse-engineers behaviour from call signatures.
//! This function hands it the same authoritative pointer every other surface
//! already ships — the extension's `llms.txt` — from inside the session it
//! already has.
//!
//! ```sql
//! SELECT goldenmatch.goldenmatch_docs();
//! ```
//!
//! Deliberately zero-argument, `IMMUTABLE`, and free of every dependency this
//! crate otherwise carries: no embedded CPython, no SPI, no GUC, no file I/O.
//! The text is `include_str!`'d at compile time, so it answers even in a
//! backend where the Python bridge would fail to initialise — which is exactly
//! the situation in which an agent most needs to be told where the docs are.
use pgrx::prelude::*;

/// The packaged `llms.txt`, baked into the shared library at build time.
const LLMS_TXT: &str = include_str!("../llms.txt");

/// Return the extension's `llms.txt`: what this surface is, where the
/// authoritative docs live, and which behaviours are decided rather than
/// incidental.
#[pg_extern(immutable, parallel_safe)]
pub fn goldenmatch_docs() -> &'static str {
    LLMS_TXT
}

#[cfg(any(test, feature = "pg_test"))]
#[pgrx::pg_schema]
mod tests {
    use pgrx::prelude::*;

    /// The whole point is that an agent on a bare connection can find the docs,
    /// so assert the three pointers it needs are actually in the returned text.
    #[pg_test]
    fn docs_names_the_authoritative_sources() {
        let text = crate::docs::goldenmatch_docs();
        assert!(text.starts_with("# goldenmatch_pg"), "got: {:.40}", text);
        assert!(text.contains("docs.bensevern.dev/docs/extensions/sql"));
        assert!(text.contains("docs.bensevern.dev/docs/llms.txt"));
        assert!(text.contains("github.com/benseverndev-oss/goldenmatch"));
    }

    /// Reachable over SQL with no arguments — the call an agent will actually make.
    #[pg_test]
    fn docs_callable_from_sql() {
        let text = Spi::get_one::<String>("SELECT goldenmatch.goldenmatch_docs()")
            .expect("SPI failed")
            .expect("goldenmatch_docs() returned NULL");
        assert!(text.contains("goldenmatch_docs"));
    }
}
