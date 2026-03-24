//! Pipeline schema functions for GoldenMatch.
//!
//! Provides job management: configure, run, inspect results.
//! All state stored in goldenmatch._jobs, _pairs, _clusters, _golden tables.

use pgrx::prelude::*;

use crate::spi;

/// Configure a named job with a JSON config.
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
        Err(e) => pgrx::error!("goldenmatch: failed to configure job: {}", e),
    })
}

/// Run a configured job against a table.
#[pg_extern]
pub fn gm_run(job_name: String, table_name: String) -> String {
    let config_query = format!(
        "SELECT config_json::text FROM goldenmatch._jobs WHERE name = '{}'",
        job_name.replace('\'', "''")
    );

    let config_json = Spi::connect(|client| {
        let result = client
            .select(&config_query, None, None)
            .unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
        for row in result {
            if let Ok(Some(cfg)) = row.get::<String>(1) {
                return Ok(cfg);
            }
        }
        Err(format!("Job '{}' not found", job_name))
    });

    let config_json = match config_json {
        Ok(c) => c,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    };

    set_job_status(&job_name, "running");

    let rows_json = match spi::read_table_as_json(&table_name) {
        Ok(json) => json,
        Err(e) => {
            set_job_status(&job_name, "failed");
            pgrx::error!("goldenmatch: {}", e);
        }
    };

    let result = match goldenmatch_bridge::api::dedupe(&rows_json, &config_json) {
        Ok(r) => r,
        Err(e) => {
            set_job_status(&job_name, "failed");
            pgrx::error!("goldenmatch: {}", e);
        }
    };

    // Store results
    let escaped = job_name.replace('\'', "''");
    Spi::connect(|mut client| {
        let _ = client.update(
            &format!(
                "DELETE FROM goldenmatch._pairs WHERE job_name = '{}'",
                escaped
            ),
            None,
            None,
        );
        let _ = client.update(
            &format!(
                "DELETE FROM goldenmatch._clusters WHERE job_name = '{}'",
                escaped
            ),
            None,
            None,
        );
        let _ = client.update(
            &format!(
                "DELETE FROM goldenmatch._golden WHERE job_name = '{}'",
                escaped
            ),
            None,
            None,
        );
    });

    // Store scored pairs
    if let Ok(pairs) = goldenmatch_bridge::api::dedupe_pairs(&rows_json, &config_json) {
        for p in &pairs {
            let insert = format!(
                "INSERT INTO goldenmatch._pairs (job_name, id_a, id_b, score) VALUES ('{}', {}, {}, {})",
                escaped, p.id_a, p.id_b, p.score
            );
            Spi::connect(|mut client| {
                let _ = client.update(&insert, None, None);
            });
        }
    }

    // Store cluster assignments
    if let Ok(members) = goldenmatch_bridge::api::dedupe_clusters(&rows_json, &config_json) {
        for m in &members {
            let insert = format!(
                "INSERT INTO goldenmatch._clusters (job_name, cluster_id, record_id) VALUES ('{}', {}, {})",
                escaped, m.cluster_id, m.record_id
            );
            Spi::connect(|mut client| {
                let _ = client.update(&insert, None, None);
            });
        }
    }

    // Store golden records
    if let Some(ref golden_json) = result.golden_json {
        let insert = format!(
            "INSERT INTO goldenmatch._golden (job_name, cluster_id, record_data) \
             SELECT '{}', (row_number() OVER ())::bigint, row_data::jsonb \
             FROM json_array_elements_text('{}'::json) AS row_data",
            escaped,
            golden_json.replace('\'', "''")
        );
        Spi::connect(|mut client| {
            let _ = client.update(&insert, None, None);
        });
    }

    set_job_status(&job_name, "completed");
    result.stats_json
}

/// List all configured jobs.
#[pg_extern]
pub fn gm_jobs() -> String {
    let query = "SELECT coalesce(json_agg(row_to_json(j))::text, '[]') \
                 FROM (SELECT name, status, created_at, last_run_at FROM goldenmatch._jobs ORDER BY created_at DESC) j";

    Spi::connect(|client| {
        let result = client
            .select(query, None, None)
            .unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
        for row in result {
            if let Ok(Some(json)) = row.get::<String>(1) {
                return json;
            }
        }
        "[]".to_string()
    })
}

/// Get golden records for a completed job.
#[pg_extern]
pub fn gm_golden(job_name: String) -> String {
    let query = format!(
        "SELECT coalesce(json_agg(record_data)::text, '[]') FROM goldenmatch._golden WHERE job_name = '{}'",
        job_name.replace('\'', "''")
    );

    Spi::connect(|client| {
        let result = client
            .select(&query, None, None)
            .unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
        for row in result {
            if let Ok(Some(json)) = row.get::<String>(1) {
                return json;
            }
        }
        "[]".to_string()
    })
}

/// Get scored pairs for a completed job as table rows.
#[pg_extern]
pub fn gm_pairs(
    job_name: String,
) -> TableIterator<'static, (name!(id_a, i64), name!(id_b, i64), name!(score, f64))> {
    let query = format!(
        "SELECT id_a, id_b, score FROM goldenmatch._pairs WHERE job_name = '{}' ORDER BY score DESC",
        job_name.replace('\'', "''")
    );

    let rows = Spi::connect(|client| {
        let result = client
            .select(&query, None, None)
            .unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
        let mut rows = Vec::new();
        for row in result {
            let id_a: i64 = row.get(1).unwrap_or(Some(0)).unwrap_or(0);
            let id_b: i64 = row.get(2).unwrap_or(Some(0)).unwrap_or(0);
            let score: f64 = row.get(3).unwrap_or(Some(0.0)).unwrap_or(0.0);
            rows.push((id_a, id_b, score));
        }
        rows
    });

    TableIterator::new(rows)
}

/// Get cluster assignments for a completed job as table rows.
#[pg_extern]
pub fn gm_clusters(
    job_name: String,
) -> TableIterator<'static, (name!(cluster_id, i64), name!(record_id, i64))> {
    let query = format!(
        "SELECT cluster_id, record_id FROM goldenmatch._clusters WHERE job_name = '{}' ORDER BY cluster_id, record_id",
        job_name.replace('\'', "''")
    );

    let rows = Spi::connect(|client| {
        let result = client
            .select(&query, None, None)
            .unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
        let mut rows = Vec::new();
        for row in result {
            let cluster_id: i64 = row.get(1).unwrap_or(Some(0)).unwrap_or(0);
            let record_id: i64 = row.get(2).unwrap_or(Some(0)).unwrap_or(0);
            rows.push((cluster_id, record_id));
        }
        rows
    });

    TableIterator::new(rows)
}

/// Drop a job and all its results.
#[pg_extern]
pub fn gm_drop(job_name: String) -> String {
    let escaped = job_name.replace('\'', "''");

    Spi::connect(|mut client| {
        for sql in [
            format!(
                "DELETE FROM goldenmatch._golden WHERE job_name = '{}'",
                escaped
            ),
            format!(
                "DELETE FROM goldenmatch._clusters WHERE job_name = '{}'",
                escaped
            ),
            format!(
                "DELETE FROM goldenmatch._pairs WHERE job_name = '{}'",
                escaped
            ),
            format!("DELETE FROM goldenmatch._jobs WHERE name = '{}'", escaped),
        ] {
            if let Err(e) = client.update(&sql, None, None) {
                pgrx::error!("goldenmatch: failed to drop job: {}", e);
            }
        }
        format!("Job '{}' dropped", job_name)
    })
}

fn set_job_status(job_name: &str, status: &str) {
    let sql = format!(
        "UPDATE goldenmatch._jobs SET status = '{}' WHERE name = '{}'",
        status.replace('\'', "''"),
        job_name.replace('\'', "''")
    );
    Spi::connect(|mut client| {
        let _ = client.update(&sql, None, None);
    });
}
