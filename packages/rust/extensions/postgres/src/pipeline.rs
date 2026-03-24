//! Pipeline schema functions for GoldenMatch.
//!
//! Provides job management: configure, run, inspect results.
//! All state stored in goldenmatch._jobs, _pairs, _clusters, _golden tables.

use pgrx::prelude::*;

use crate::spi;

/// Configure a named job with a JSON config.
///
/// ```sql
/// CALL goldenmatch.configure('my_job', '{"exact": ["email"], "fuzzy": {"name": 0.85}}');
/// ```
#[pg_extern]
pub fn gm_configure(job_name: String, config_json: String) -> String {
    let upsert = format!(
        "INSERT INTO goldenmatch._jobs (name, config_json, created_at, status) \
         VALUES ('{}', '{}'::jsonb, now(), 'configured') \
         ON CONFLICT (name) DO UPDATE SET config_json = EXCLUDED.config_json, status = 'configured'",
        job_name.replace('\'', "''"),
        config_json.replace('\'', "''")
    );

    Spi::connect(|mut client| match client.update(&upsert, None, None) {
        Ok(_) => format!("Job '{}' configured", job_name),
        Err(e) => format!("Error configuring job: {}", e),
    })
}

/// Run a configured job against a table.
///
/// ```sql
/// SELECT goldenmatch.gm_run('my_job', 'customers');
/// ```
#[pg_extern]
pub fn gm_run(job_name: String, table_name: String) -> String {
    // 1. Load job config
    let config_query = format!(
        "SELECT config_json::text FROM goldenmatch._jobs WHERE name = '{}'",
        job_name.replace('\'', "''")
    );

    let config_json = Spi::connect(|client| {
        let result = client.select(&config_query, None, None);
        match result {
            Ok(table) => {
                for row in table {
                    if let Ok(Some(cfg)) = row.get::<String>(1) {
                        return Ok(cfg);
                    }
                }
                Err(format!(
                    "Job '{}' not found. Run goldenmatch.gm_configure() first.",
                    job_name
                ))
            }
            Err(e) => Err(format!("Error loading job config: {}", e)),
        }
    });

    let config_json = match config_json {
        Ok(c) => c,
        Err(e) => return format!("{{\"error\": \"{}\"}}", e),
    };

    // 2. Mark as running
    let update_status = format!(
        "UPDATE goldenmatch._jobs SET status = 'running', last_run_at = now() WHERE name = '{}'",
        job_name.replace('\'', "''")
    );
    Spi::connect(|mut client| {
        let _ = client.update(&update_status, None, None);
    });

    // 3. Read table data via SPI
    let rows_json = match spi::read_table_as_json(&table_name) {
        Ok(json) => json,
        Err(e) => {
            set_job_status(&job_name, "failed");
            return format!("{{\"error\": \"{}\"}}", e);
        }
    };

    // 4. Run dedupe via bridge
    let result = match goldenmatch_bridge::api::dedupe(&rows_json, &config_json) {
        Ok(r) => r,
        Err(e) => {
            set_job_status(&job_name, "failed");
            return format!("{{\"error\": \"{}\"}}", e);
        }
    };

    // 5. Store results
    // Clear previous results for this job
    let clear_sql = format!(
        "DELETE FROM goldenmatch._pairs WHERE job_name = '{}'; \
         DELETE FROM goldenmatch._clusters WHERE job_name = '{}'; \
         DELETE FROM goldenmatch._golden WHERE job_name = '{}'",
        job_name.replace('\'', "''"),
        job_name.replace('\'', "''"),
        job_name.replace('\'', "''")
    );
    Spi::connect(|mut client| {
        let _ = client.update(&clear_sql, None, None);
    });

    // Store golden records if available
    if let Some(ref golden_json) = result.golden_json {
        let insert = format!(
            "INSERT INTO goldenmatch._golden (job_name, cluster_id, record_data) \
             SELECT '{}', (row_number() OVER ())::bigint, row_data::jsonb \
             FROM json_array_elements_text('{}'::json) AS row_data",
            job_name.replace('\'', "''"),
            golden_json.replace('\'', "''")
        );
        Spi::connect(|mut client| {
            let _ = client.update(&insert, None, None);
        });
    }

    // 6. Mark completed
    set_job_status(&job_name, "completed");

    // Return stats
    result.stats_json
}

/// List all configured jobs.
///
/// ```sql
/// SELECT * FROM goldenmatch.gm_jobs();
/// ```
#[pg_extern]
pub fn gm_jobs() -> String {
    let query = "SELECT json_agg(row_to_json(j))::text \
                 FROM (SELECT name, status, created_at, last_run_at FROM goldenmatch._jobs ORDER BY created_at DESC) j";

    Spi::connect(|client| {
        let result = client.select(query, None, None);
        match result {
            Ok(table) => {
                for row in table {
                    if let Ok(Some(json)) = row.get::<String>(1) {
                        return json;
                    }
                }
                "[]".to_string()
            }
            Err(e) => format!("{{\"error\": \"{}\"}}", e),
        }
    })
}

/// Get golden records for a completed job.
///
/// ```sql
/// SELECT goldenmatch.gm_golden('my_job');
/// ```
#[pg_extern]
pub fn gm_golden(job_name: String) -> String {
    let query = format!(
        "SELECT json_agg(record_data)::text FROM goldenmatch._golden WHERE job_name = '{}'",
        job_name.replace('\'', "''")
    );

    Spi::connect(|client| {
        let result = client.select(&query, None, None);
        match result {
            Ok(table) => {
                for row in table {
                    if let Ok(Some(json)) = row.get::<String>(1) {
                        return json;
                    }
                }
                "[]".to_string()
            }
            Err(e) => format!("{{\"error\": \"{}\"}}", e),
        }
    })
}

/// Drop a job and all its results.
///
/// ```sql
/// SELECT goldenmatch.gm_drop('my_job');
/// ```
#[pg_extern]
pub fn gm_drop(job_name: String) -> String {
    let escaped = job_name.replace('\'', "''");
    let sql = format!(
        "DELETE FROM goldenmatch._golden WHERE job_name = '{}'; \
         DELETE FROM goldenmatch._clusters WHERE job_name = '{}'; \
         DELETE FROM goldenmatch._pairs WHERE job_name = '{}'; \
         DELETE FROM goldenmatch._jobs WHERE name = '{}'",
        escaped, escaped, escaped, escaped
    );

    Spi::connect(|mut client| match client.update(&sql, None, None) {
        Ok(_) => format!("Job '{}' dropped", job_name),
        Err(e) => format!("Error dropping job: {}", e),
    })
}

fn set_job_status(job_name: &str, status: &str) {
    let sql = format!(
        "UPDATE goldenmatch._jobs SET status = '{}' WHERE name = '{}'",
        status,
        job_name.replace('\'', "''")
    );
    Spi::connect(|mut client| {
        let _ = client.update(&sql, None, None);
    });
}
