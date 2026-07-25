//! Pipeline schema functions for GoldenMatch.
//!
//! Provides job management: configure, run, inspect results.
//! All state stored in goldenmatch._jobs, _pairs, _clusters, _golden tables.

use pgrx::prelude::*;

use crate::spi;

/// Rows per batched `INSERT ... VALUES (...),(...)` when persisting `gm_run`
/// results. Bounds each statement's size while turning the former one-round-trip
/// -per-row writeback into `ceil(n / INSERT_CHUNK)` statements (#1883).
const INSERT_CHUNK: usize = 1000;

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

    // Read once into a single TableData (columnar Arrow when the columns are
    // parity-safe, JSON fallback otherwise).
    let table_data = match spi::read_table(&table_name) {
        Ok(t) => t,
        Err(e) => {
            set_job_status(&job_name, "failed");
            pgrx::error!("goldenmatch: {}", e);
        }
    };

    // Run the engine ONCE (#1883): the bundle carries golden/stats/telemetry +
    // scored pairs + cluster assignments off a single pipeline run, instead of
    // three separate `dedupe`/`dedupe_pairs`/`dedupe_clusters` calls that each
    // re-ran the full pipeline (~3x the compute) and — because the pipeline is
    // non-deterministic run-to-run — could persist mutually-inconsistent
    // pairs/clusters/golden. One run makes the three outputs consistent.
    let bundle = match goldenmatch_bridge::api::dedupe_bundle(&table_data, &config_json) {
        Ok(b) => b,
        Err(e) => {
            set_job_status(&job_name, "failed");
            pgrx::error!("goldenmatch: {}", e);
        }
    };
    let result = bundle.result;

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

    // Store scored pairs. Batched: one multi-row INSERT per chunk inside a
    // single Spi::connect, instead of a Spi::connect + single-row INSERT PER
    // pair (#1883). At scale that per-row SPI connect/exec was O(pairs) round
    // trips; chunking bounds the statement size while collapsing them to
    // O(pairs / CHUNK).
    {
        let pairs = &bundle.pairs;
        for chunk in pairs.chunks(INSERT_CHUNK) {
            let values: Vec<String> = chunk
                .iter()
                .map(|p| format!("('{}', {}, {}, {})", escaped, p.id_a, p.id_b, p.score))
                .collect();
            let insert = format!(
                "INSERT INTO goldenmatch._pairs (job_name, id_a, id_b, score) VALUES {}",
                values.join(",")
            );
            Spi::connect(|mut client| {
                let _ = client.update(&insert, None, None);
            });
        }
    }

    // Store cluster assignments (batched, same rationale as the pairs above).
    {
        let members = &bundle.clusters;
        for chunk in members.chunks(INSERT_CHUNK) {
            let values: Vec<String> = chunk
                .iter()
                .map(|m| format!("('{}', {}, {})", escaped, m.cluster_id, m.record_id))
                .collect();
            let insert = format!(
                "INSERT INTO goldenmatch._clusters (job_name, cluster_id, record_id) VALUES {}",
                values.join(",")
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

    // Persist controller telemetry on the job row (NULL when explicit config).
    if let Some(ref telemetry) = result.telemetry_json {
        let update = format!(
            "UPDATE goldenmatch._jobs SET last_telemetry_json = '{}'::jsonb, last_run_at = now() WHERE name = '{}'",
            telemetry.replace('\'', "''"),
            escaped
        );
        Spi::connect(|mut client| {
            let _ = client.update(&update, None, None);
        });
    } else {
        let update = format!(
            "UPDATE goldenmatch._jobs SET last_telemetry_json = NULL, last_run_at = now() WHERE name = '{}'",
            escaped
        );
        Spi::connect(|mut client| {
            let _ = client.update(&update, None, None);
        });
    }

    set_job_status(&job_name, "completed");
    result.stats_json
}

/// Return the controller telemetry from the most recent `gm_run` of a job.
///
/// Returns `'{"available":false}'` if the job hasn't run or the run used an
/// explicit config (controller never fired).
#[pg_extern]
pub fn gm_telemetry(job_name: String) -> String {
    let query = format!(
        "SELECT coalesce(last_telemetry_json::text, '{{\"available\":false}}') \
         FROM goldenmatch._jobs WHERE name = '{}'",
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
        "{\"available\":false}".to_string()
    })
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

/// Resolve a configured job's table into a Postgres-native identity dataset
/// (#1913 P1 — the in-DB stateful write path).
///
/// Loads `job_name`'s stored config, reads `table_name`, and hands them to the
/// bridge `resolve_identities`, which runs `dedupe_df` with the identity graph
/// pointed at the DSN from the `goldenmatch.identity_dsn` GUC (or the backend
/// env). The durable graph (nodes / source_records / evidence_edges / events)
/// is written into that database; re-running against the same `dataset`
/// absorbs new records into existing stable ids (incremental resolve).
///
/// Returns the identity resolution summary JSON (`created` / `absorbed_records`
/// / `merged` / `edges_added` / `events_emitted` / `conflicts_flagged`).
///
/// NOTE (transaction isolation, per the #1913 design §3.1): the identity writes
/// commit on the bridge's own libpq connection, NOT the caller's SQL
/// transaction. `gm_resolve` is a batch op; replay is idempotent
/// (`has_run_event` / edge-UNIQUE guards), so a failed run converges on re-run.
#[pg_extern]
pub fn gm_resolve(job_name: String, table_name: String, dataset: String) -> String {
    let dsn = resolve_identity_dsn().unwrap_or_else(|| {
        pgrx::error!(
            "goldenmatch: in-DB identity resolution needs a DSN — set \
             `ALTER SYSTEM SET goldenmatch.identity_dsn = '...'` (or the \
             GOLDENMATCH_IDENTITY_DSN / GOLDENMATCH_DATABASE_URL backend env) \
             pointing at this database"
        )
    });

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

    let table_data = match spi::read_table(&table_name) {
        Ok(t) => t,
        Err(e) => {
            set_job_status(&job_name, "failed");
            pgrx::error!("goldenmatch: {}", e);
        }
    };

    // A distinct run name per call (microsecond clock stamp) so a second
    // resolve emits fresh absorb/merge events rather than being deduped as a
    // replay of the first — the incremental-across-runs contract.
    let stamp = Spi::get_one::<String>("SELECT to_char(clock_timestamp(), 'YYYYMMDDHH24MISSUS')")
        .ok()
        .flatten()
        .unwrap_or_default();
    let run_name = format!("gm_resolve:{}:{}", job_name.replace(':', "_"), stamp);

    let summary_json = match goldenmatch_bridge::api::resolve_identities(
        &table_data,
        &config_json,
        &dsn,
        &dataset,
        &run_name,
    ) {
        Ok(s) => s,
        Err(e) => {
            set_job_status(&job_name, "failed");
            pgrx::error!("goldenmatch: {}", e);
        }
    };

    set_job_status(&job_name, "completed");
    summary_json
}

/// Steward manual **merge** of two identities in the in-DB dataset (#1913 P3).
///
/// `entity_a` is kept, `entity_b` is absorbed into it (its source records
/// reassigned, the identity retired), with a `manual_merge` event on both.
/// Delegates to the Python steward path (`manual_merge`) through the bridge over
/// the `goldenmatch.identity_dsn` store — the same engine `goldenmatch identity
/// merge` uses. Returns the result JSON (`{"keep", "absorbed", "at"}`).
///
/// `dataset` is accepted for API symmetry with `gm_resolve`; the identity ids
/// (globally-unique UUIDv7) fully identify the entities, so it is used only as
/// diagnostic context (entity ids are not dataset-scoped in the store).
#[pg_extern]
pub fn gm_identity_merge(dataset: String, entity_a: String, entity_b: String) -> String {
    let dsn = resolve_identity_dsn().unwrap_or_else(|| {
        pgrx::error!(
            "goldenmatch: in-DB identity merge needs a DSN — set \
             `ALTER SYSTEM SET goldenmatch.identity_dsn = '...'` (or the \
             GOLDENMATCH_IDENTITY_DSN / GOLDENMATCH_DATABASE_URL backend env) \
             pointing at this database"
        )
    });
    match goldenmatch_bridge::api::identity_merge(&dsn, &entity_a, &entity_b, "") {
        Ok(s) => s,
        Err(e) => pgrx::error!(
            "goldenmatch: gm_identity_merge(dataset={}, keep={}, absorb={}) failed: {}",
            dataset,
            entity_a,
            entity_b,
            e
        ),
    }
}

/// Steward manual **split** of a record out of an identity in the in-DB dataset
/// (#1913 P3).
///
/// Moves `record_id` into a fresh identity, with a `manual_split` event on both
/// the original and the new entity. Delegates to the Python steward path
/// (`manual_split`) through the bridge over the `goldenmatch.identity_dsn`
/// store. Returns the result JSON (`{"new_entity_id", "moved", "at"}`).
///
/// `dataset` is accepted for API symmetry with `gm_resolve` (used as diagnostic
/// context only — the identity/record ids are not dataset-scoped in the store).
#[pg_extern]
pub fn gm_identity_split(dataset: String, entity_id: String, record_id: String) -> String {
    let dsn = resolve_identity_dsn().unwrap_or_else(|| {
        pgrx::error!(
            "goldenmatch: in-DB identity split needs a DSN — set \
             `ALTER SYSTEM SET goldenmatch.identity_dsn = '...'` (or the \
             GOLDENMATCH_IDENTITY_DSN / GOLDENMATCH_DATABASE_URL backend env) \
             pointing at this database"
        )
    });
    match goldenmatch_bridge::api::identity_split(&dsn, &entity_id, &record_id, "") {
        Ok(s) => s,
        Err(e) => pgrx::error!(
            "goldenmatch: gm_identity_split(dataset={}, entity={}, record={}) failed: {}",
            dataset,
            entity_id,
            record_id,
            e
        ),
    }
}

// ── Identity audit / mediation writes (post-#1913) ───────────────────────
//
// Steward writes into the in-DB Postgres identity dataset, DSN-resolved via
// `resolve_identity_dsn()` like `gm_identity_merge`/`gm_identity_split`. Empty
// optional args (`reason`, `dataset`) mean "unset". Like all in-DB identity
// writes (§3.1 of the #1913 design), these commit on the store's own libpq
// connection, NOT the caller's SQL transaction; replay is idempotent.

/// Seal the audit log for tamper-evidence (steward write). Returns
/// `{"sealed": false, ...}` when there is nothing new to seal, else the new
/// seal-anchor JSON. `dataset` empty = the global seal chain.
#[pg_extern]
pub fn gm_identity_audit_seal(dataset: String) -> String {
    let dsn = resolve_identity_dsn().unwrap_or_else(|| {
        pgrx::error!(
            "goldenmatch: in-DB audit seal needs a DSN — set \
             `ALTER SYSTEM SET goldenmatch.identity_dsn = '...'` (or the \
             GOLDENMATCH_IDENTITY_DSN / GOLDENMATCH_DATABASE_URL backend env) \
             pointing at this database"
        )
    });
    match goldenmatch_bridge::api::identity_audit_seal(&dsn, &dataset, "") {
        Ok(s) => s,
        Err(e) => pgrx::error!(
            "goldenmatch: gm_identity_audit_seal(dataset={}) failed: {}",
            dataset,
            e
        ),
    }
}

/// Steward conflict mediation (steward write). `resolution` ∈ `same` /
/// `distinct` / `defer`; `same` keeps the entity, `distinct` splits `record_b`
/// out, `defer` only logs. Empty `reason` / `dataset` mean "unset". Returns the
/// mediation result JSON.
#[pg_extern]
pub fn gm_identity_resolve_conflict(
    dataset: String,
    record_a: String,
    record_b: String,
    resolution: String,
    reason: String,
) -> String {
    let dsn = resolve_identity_dsn().unwrap_or_else(|| {
        pgrx::error!(
            "goldenmatch: in-DB conflict mediation needs a DSN — set \
             `ALTER SYSTEM SET goldenmatch.identity_dsn = '...'` (or the \
             GOLDENMATCH_IDENTITY_DSN / GOLDENMATCH_DATABASE_URL backend env) \
             pointing at this database"
        )
    });
    match goldenmatch_bridge::api::identity_resolve_conflict(
        &dsn,
        &record_a,
        &record_b,
        &resolution,
        &reason,
        &dataset,
    ) {
        Ok(s) => s,
        Err(e) => pgrx::error!(
            "goldenmatch: gm_identity_resolve_conflict(a={}, b={}, resolution={}) failed: {}",
            record_a,
            record_b,
            resolution,
            e
        ),
    }
}

/// Steward claim (steward write): attach `record_id` to `entity_id`, moving it
/// out of any prior entity + emitting a `claimed` event. Empty `reason` = unset.
/// Returns the claim result JSON.
#[pg_extern]
pub fn gm_identity_claim(entity_id: String, record_id: String, reason: String) -> String {
    let dsn = resolve_identity_dsn().unwrap_or_else(|| {
        pgrx::error!(
            "goldenmatch: in-DB identity claim needs a DSN — set \
             `ALTER SYSTEM SET goldenmatch.identity_dsn = '...'` (or the \
             GOLDENMATCH_IDENTITY_DSN / GOLDENMATCH_DATABASE_URL backend env) \
             pointing at this database"
        )
    });
    match goldenmatch_bridge::api::identity_claim(&dsn, &entity_id, &record_id, &reason) {
        Ok(s) => s,
        Err(e) => pgrx::error!(
            "goldenmatch: gm_identity_claim(entity={}, record={}) failed: {}",
            entity_id,
            record_id,
            e
        ),
    }
}

/// Resolve the identity DSN: the `goldenmatch.identity_dsn` GUC first, then the
/// `GOLDENMATCH_IDENTITY_DSN` / `GOLDENMATCH_DATABASE_URL` backend env. Returns
/// `None` (→ a clear error at the call site) when nothing is configured. A
/// blank/whitespace value is treated as unset. Shared with the read-only
/// `goldenmatch_identity_*` functions (#1913 P2), which pass the DSN as the
/// store ref when the caller supplies an empty `db_path`.
pub(crate) fn resolve_identity_dsn() -> Option<String> {
    if let Some(cstr) = crate::IDENTITY_DSN.get() {
        if let Ok(s) = cstr.to_str() {
            if !s.trim().is_empty() {
                return Some(s.to_string());
            }
        }
    }
    for var in ["GOLDENMATCH_IDENTITY_DSN", "GOLDENMATCH_DATABASE_URL"] {
        if let Ok(s) = std::env::var(var) {
            if !s.trim().is_empty() {
                return Some(s);
            }
        }
    }
    None
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
