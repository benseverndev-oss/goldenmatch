use pgrx::prelude::*;

pgrx::pg_module_magic!();

mod quick;
mod spi;

#[cfg(test)]
pub mod pg_test {
    pub fn setup(_options: Vec<&str>) {}

    pub fn postgresql_conf_options() -> Vec<&'static str> {
        vec![]
    }
}
