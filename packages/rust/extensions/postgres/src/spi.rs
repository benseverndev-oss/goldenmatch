//! SPI-based table reading for GoldenMatch.
//!
//! Uses Postgres Server Programming Interface to read table data
//! and convert it to JSON for the Python bridge.

use pgrx::prelude::*;
use pgrx::spi;

/// Read all rows from a table and return as a JSON array string.
///
/// Uses SPI to execute `SELECT * FROM <table>` and converts each row
/// to a JSON object. Column types are mapped to JSON types:
/// - TEXT/VARCHAR -> JSON string
/// - INT/BIGINT -> JSON number
/// - FLOAT/DOUBLE -> JSON number
/// - BOOL -> JSON boolean
/// - NULL -> JSON null
/// - Everything else -> JSON string via ::text cast
pub fn read_table_as_json(table_name: &str) -> Result<String, String> {
    // Validate table name (allow only alphanumeric, underscore, dot for schema.table)
    if !table_name
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_' || c == '.')
    {
        return Err(format!("Invalid table name: {}", table_name));
    }

    let query = format!(
        "SELECT row_to_json(t)::text FROM (SELECT * FROM {}) t",
        table_name
    );

    Spi::connect(|client| {
        let result = client.select(&query, None, None);

        match result {
            Ok(table) => {
                let mut rows: Vec<String> = Vec::new();
                for row in table {
                    if let Ok(Some(json_str)) = row.get::<String>(1) {
                        rows.push(json_str);
                    }
                }
                Ok(format!("[{}]", rows.join(",")))
            }
            Err(e) => Err(format!("SPI error reading table '{}': {}", table_name, e)),
        }
    })
}
