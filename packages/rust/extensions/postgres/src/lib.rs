use pgrx::guc::{GucContext, GucFlags, GucRegistry, GucSetting};
use pgrx::prelude::*;
use std::ffi::CStr;

pgrx::pg_module_magic!();

mod core_apis;
mod correction;
mod goldencheck_kernels;
mod goldenflow;
mod kernels;
mod pipeline;
mod quick;
mod spi;

/// libpq DSN for the in-DB identity store used by `gm_resolve()` (#1913).
///
/// Superuser-set (`ALTER SYSTEM SET goldenmatch.identity_dsn = '...'` or a
/// session `SET`). When unset, `gm_resolve()` falls back to the backend
/// `GOLDENMATCH_IDENTITY_DSN` / `GOLDENMATCH_DATABASE_URL` env, and errors
/// clearly if none is configured.
pub static IDENTITY_DSN: GucSetting<Option<&'static CStr>> =
    GucSetting::<Option<&'static CStr>>::new(None);

#[pg_guard]
pub extern "C" fn _PG_init() {
    GucRegistry::define_string_guc(
        "goldenmatch.identity_dsn",
        "libpq DSN for the in-DB identity store used by gm_resolve().",
        "When set, gm_resolve() opens a second libpq connection to this DSN and \
         writes the durable, event-sourced identity graph there (the same \
         database the extension runs in). Superuser-set; falls back to the \
         GOLDENMATCH_IDENTITY_DSN / GOLDENMATCH_DATABASE_URL backend env.",
        &IDENTITY_DSN,
        GucContext::Suset,
        GucFlags::default(),
    );
}

#[cfg(test)]
pub mod pg_test {
    pub fn setup(_options: Vec<&str>) {}

    pub fn postgresql_conf_options() -> Vec<&'static str> {
        vec![]
    }
}
